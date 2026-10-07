"""Local validation runner over existing backend functions, no synthetic outputs.

Requires authorized, deidentified images. Writes only to Git-ignored outputs/e2e.
It does not send a mock delivery or import records into the KR4 history dataset.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', nargs='*', type=Path, default=[])
    parser.add_argument('--authorized', action='store_true')
    parser.add_argument('--deidentified', action='store_true')
    args = parser.parse_args()
    out = ROOT/'outputs'/'e2e'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    metadata = {'status': 'NOT_STARTED', 'data_source': 'user_upload', 'case_id': None,
                'latencies_seconds': {}, 'token_usage': None, 'warnings': [],
                'real_e2e_passed': False, 'external_llm_called': False}
    def write(name, value):
        if hasattr(value, 'model_dump'):
            value = value.model_dump(mode='json')
        (out/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    if not args.images or any(not p.is_file() or p.suffix.lower() not in {'.jpg','.jpeg','.png','.bmp','.webp'} for p in args.images):
        metadata.update(status='BLOCKED_BY_TEST_DATA', reason='需要1–3张（或更多）已授权脱敏医疗图片，不能用缓存JSON或生成官方病例替代')
        write('run_metadata', metadata)
        print(f'BLOCKED_BY_TEST_DATA: {out}')
        return 2
    if not args.authorized or not args.deidentified:
        metadata.update(status='BLOCKED_BY_AUTHORIZATION', reason='必须核实已授权且已脱敏，再显式传 --authorized --deidentified')
        write('run_metadata', metadata)
        print(f'BLOCKED_BY_AUTHORIZATION: {out}')
        return 2
    from dotenv import load_dotenv
    load_dotenv(ROOT/'web/backend/.env')
    if not os.getenv('DEEPSEEK_API_KEY'):
        metadata.update(status='BLOCKED_BY_CONFIGURATION', reason='未配置本地 DEEPSEEK_API_KEY')
        write('run_metadata', metadata)
        print(f'BLOCKED_BY_CONFIGURATION: {out}')
        return 2
    from fastapi import UploadFile
    from web.backend import app as backend
    from medical_claims.storage import CaseStorage, encode
    from medical_claims.workflow_models import CreateCaseRequest
    from medical_claims.delivery import prepare_delivery_content
    os.environ['LONG_CASE_CACHE_ENABLED'] = 'false'  # Require a real structuring API call.
    storage = backend.get_case_storage()
    start = perf_counter()
    stage = 'create_case'
    calls = []
    original_post = backend.post_deepseek
    def recorded_post(body):
        metadata['external_llm_called'] = True
        response = original_post(body)
        calls.append({'stage': stage, 'usage': response.get('usage')})
        return response
    def timed(name, call):
        nonlocal stage
        stage = name
        before = perf_counter()
        try:
            return call()
        finally:
            metadata['latencies_seconds'][name] = round(perf_counter()-before, 4)
    backend.post_deepseek = recorded_post
    try:
        created = storage.create_case(CreateCaseRequest(claim_event_id='real-e2e-'+out.name,
            data_source='user_upload',consent_status='authorized',deidentification_status='confirmed'))
        metadata['case_id'] = created.case_id
        storage.begin_processing(created.case_id)
        with tempfile.TemporaryDirectory(prefix='medical-e2e-') as tmp:
            handles = [p.open('rb') for p in args.images]
            try:
                uploads = [UploadFile(filename=p.name,file=h) for p,h in zip(args.images,handles)]
                documents = timed('ocr', lambda: asyncio.run(backend.recognize_uploaded_documents(uploads, Path(tmp))))
            finally:
                for h in handles:
                    h.close()
        storage.save_ocr(created.case_id, documents)
        write('ocr', documents)
        metadata['ocr_success'] = any(d.get('full_text','').strip() for d in documents)
        if not metadata['ocr_success']:
            raise ValueError('OCR没有可用文字，停止，不使用固定demo')
        case = timed('structuring', lambda: backend.structure_case_with_long_document_support(created.case_id, documents))
        storage.save_structured(created.case_id, case)
        write('claim_case',case)
        metadata['deepseek_structuring_success'] = any(c['stage']=='structuring' for c in calls)
        pain = timed('pain',lambda:backend.analyze_case_pain_points(backend.PainAnalysisRequest(case=case,use_llm=True)))
        write('pain_report',pain)
        care = timed('care',lambda:backend.generate_case_care_guide(backend.CareGuideRequest(case=case,pain_report=pain,use_llm=True)))
        write('care_guide',care)
        gate = timed('risk_gate',lambda:storage.prepare_delivery(prepare_delivery_content(storage,created.case_id)))
        write('risk_gate',gate)
        reloaded = CaseStorage(storage.path)
        saved = reloaded.get_results(created.case_id)
        assert encode(saved['ocr_result'])==encode(documents)
        assert encode(saved['structured_result'])==encode(case)
        assert encode(saved['pain_result'])==encode(pain)
        assert encode(saved['care_result'])==encode(care)
        metadata['sqlite_reload_success'] = True
        metadata['final_workflow_status'] = reloaded.get_case(created.case_id)['status']
        metadata['warnings'] = [f.message for f in case.quality_flags]+pain.analysis_warnings+care.warnings+gate['reasons']
        metadata['care_generation_method'] = care.generation_method.value
        metadata['deepseek_care_success'] = care.generation_method.value=='llm'
        metadata['real_e2e_passed'] = metadata['deepseek_structuring_success'] and metadata['deepseek_care_success']
        metadata['status'] = 'EXECUTED' if metadata['real_e2e_passed'] else 'EXECUTED_WITH_FALLBACK'
        # A BLOCKED gate is recorded faithfully, never changed into a delivery.
    except Exception as exc:
        metadata.update(status='FAILED', failed_stage=stage, error_type=type(exc).__name__, error=str(exc))
        if metadata['case_id']:
            if storage.get_case(metadata['case_id'])['status']=='PROCESSING':
                storage.finish_failed_processing(metadata['case_id'])
            metadata['final_workflow_status']=storage.get_case(metadata['case_id'])['status']
    finally:
        backend.post_deepseek=original_post
        metadata['latencies_seconds']['total']=round(perf_counter()-start,4)
        metadata['deepseek_calls']=[{'stage':c['stage'],'usage_available':c['usage'] is not None} for c in calls]
        usage=[c for c in calls if c['usage'] is not None]
        metadata['token_usage']=usage or None  # Do not invent token totals.
        write('run_metadata',metadata)
    print(f"{metadata['status']}: {out}")
    return 0 if metadata['status']=='EXECUTED' else 1

if __name__=='__main__':
    raise SystemExit(main())
