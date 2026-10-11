// Run with: node tests/frontend_hardening.test.cjs
// Mock DOM regression, explicitly not a real-browser E2E test.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

async function runFrontendHardening(root = path.resolve(__dirname, '..')) {
  const source = fs.readFileSync(path.join(root, 'web/dist/app.js'), 'utf8');
  const htmlSource = fs.readFileSync(path.join(root, 'web/dist/index.html'), 'utf8');
  const styleSource = fs.readFileSync(path.join(root, 'web/dist/styles.css'), 'utf8');
  const phase1Catalog = JSON.parse(fs.readFileSync(path.join(root, 'knowledge/quality/disease_provider_catalog.json'), 'utf8'));
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'caseId' ? 'DEMO-RESP-001' : '', innerHTML: '', textContent: '', disabled: false,
      hidden: false, dataset: {}, style: {}, listeners: {}, classList: {add(){},remove(){},toggle(){}},
      addEventListener(name, fn){this.listeners[name]=fn;}, querySelectorAll(){return [];},
      querySelector(sel){return element(id+sel);}, click(){}, showModal(){}, close(){}
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document: {getElementById: element, querySelector: element,
      querySelectorAll(sel){return sel === '#pipelineSteps li' ? Array.from({length:6},(_,i)=>element('step'+i)) : [];}},
    window: {location:{search:'',href:'http://localhost:8765/'},history:{replaceState(){}}},
    history: {replaceState(){}}, localStorage: {getItem(){return null;},setItem(){},removeItem(){}},
    structuredClone, URL, URLSearchParams, console, setTimeout(){}, clearTimeout(){},
    FormData: class MockFormData {
      constructor(){this.values=[];}
      append(key,value,name){this.values.push({key,value,name});}
      get(key){const found=this.values.find(item=>item.key===key);return found&&found.value;}
    }
  });
  new vm.Script(source).runInContext(context);
  const run = code => vm.runInContext(code,context);
  let checks = 0;
  function check(condition) {assert.ok(condition); checks++;}
  check(!element('folderInput').disabled && element('runBatchButton').disabled && element('exportBatchButton').disabled);
  check(element('metricConfidence').textContent === '—');
  element('batchConsent').checked=true;element('batchDeidentified').checked=true;
  run(`backendUrl='http://localhost:8000';loadBatchFolder([
    {name:'a.png',type:'image/png',webkitRelativePath:'总目录/病例一/a.png'},
    {name:'b.png',type:'image/png',webkitRelativePath:'总目录/病例二/b.png'},
    {name:'note.txt',type:'text/plain',webkitRelativePath:'总目录/病例二/note.txt'},
    {name:'c.png',type:'image/png',webkitRelativePath:'总目录/病例三/c.png'},
    {name:'readme.txt',type:'text/plain',webkitRelativePath:'总目录/无图片病例/readme.txt'}
  ])`);
  check(run("workflowCase===null && batchCases.length===3 && batchCases.every(item=>item.status==='waiting')"));
  check(run("batchScan.total_images===3 && batchScan.unsupported_files.length===2 && batchScan.empty_directories.length===1"));
  check(!element('runBatchButton').disabled);
  let created=0;let failCase1=true;const calls=[];const fileBindings={};
  const claimCases={
    'CASE-1':run("emptyCase('CASE-1')"),'CASE-2':run("emptyCase('CASE-2')"),'CASE-3':run("emptyCase('CASE-3')")
  };
  Object.values(claimCases).forEach(item=>{item.quality_flags=[];});
  function response(payload,ok=true,status=200){return {ok,status,text:async()=>JSON.stringify(payload),json:async()=>payload};}
  context.fetch=async(url,options={})=>{
    calls.push(url);
    if(url.endsWith('/api/workflow/cases')&&options.method==='POST')return response({case_id:'CASE-'+(++created),status:'RECEIVED'});
    if(url.endsWith('/api/cases/process-detailed')){
      const caseId=options.body.get('case_id');
      fileBindings[caseId]=options.body.values.filter(item=>item.key==='files').map(item=>item.value.name);
      if(caseId==='CASE-1'&&failCase1)return response({detail:'fixture OCR failure'},false,500);
      return response({case:claimCases[caseId],documents:[{document_id:fileBindings[caseId][0],page:1,full_text:'fixture',blocks:[]}]});
    }
    if(url.endsWith('/api/cases/analyze-pain-points')){const id=JSON.parse(options.body).case.case_id;return response({case_id:id,summary:{total:1},pain_points:[]});}
    if(url.endsWith('/api/cases/generate-care-guide')){const id=JSON.parse(options.body).case.case_id;return response({case_id:id,review_required:false,safety_checks:[]});}
    const resultMatch=url.match(/\/api\/workflow\/cases\/(CASE-\d+)\/result$/);
    if(resultMatch){const id=resultMatch[1];const ready=id!=='CASE-1'||!failCase1;return response({case_id:id,structured_result:ready?claimCases[id]:null,pain_result:ready?{case_id:id}:null,care_result:ready?{case_id:id}:null,persisted_marker:ready});}
    const caseMatch=url.match(/\/api\/workflow\/cases\/(CASE-\d+)$/);
    if(caseMatch)return response({case_id:caseMatch[1],status:caseMatch[1]==='CASE-1'&&failCase1?'RECEIVED':'CARE_READY',has_structured_result:true,has_pain_result:true,has_care_result:true});
    throw Error('unexpected batch URL '+url);
  };
  await run('runBatch()');
  check(created===3 && run("new Set(batchCases.map(item=>item.case_id)).size===3"));
  check(run("batchCases[0].status==='failed' && batchCases[1].status==='completed' && batchCases[2].status==='completed'"));
  check(JSON.stringify(fileBindings)==='{"CASE-1":["a.png"],"CASE-2":["b.png"],"CASE-3":["c.png"]}');
  check(calls.some(url=>url.endsWith('/api/cases/generate-care-guide')));
  check(element('batchProgressBar').style.width==='100.0%');
  check(run("batchExportPayload().cases.length===3 && batchExportPayload().cases[1].result.persisted_marker===true"));
  check(run("batchExportPayload().cases[0].error==='fixture OCR failure'"));
  await run('openBatchInWorkbench(1)');
  check(run("workflowCase.case_id==='CASE-2' && result.case_id==='CASE-2'"));
  failCase1=false;const createdBeforeRetry=created;
  await run('retryBatchCase(0)');
  check(created===createdBeforeRetry && run("batchCases[0].case_id==='CASE-1' && batchCases[0].status==='completed'"));
  check(!source.includes('item.completeness = 90')&&!source.includes('count / 18')&&!source.includes('BATCH_DISABLED_MESSAGE'));
  function seedA() {
    run(`resetCaseState('A');backendUrl='http://localhost:8000';workflowCase={case_id:'A',has_structured_result:true};
      result=emptyCase('A');painReport={case_id:'A'};careReport={case_id:'A'};qualityAudit={case_id:'A'};
      persistedPortfolio={};currentRecommendations={case_id:'A'};qualityContext={case_id:'A'};
      qualityCoverageReport={};deliveryReadiness={case_id:'A'};
      inboxDeliveries=[{case_id:'A'}];deliveryFeedback=[{case_id:'A'}];deliveryFollowUps=[{case_id:'A'}];activeDeliveryId='old';
      document.getElementById('workflowDeidentified').checked=true;
      document.getElementById('workflowSensitivePatternReviewed').checked=true;`);
  }
  seedA(); element('caseId').value='B';element('caseId').listeners.change();
  check(run('result.case_id') === 'B');
  check(run('[workflowCase,painReport,careReport,qualityAudit,persistedPortfolio,currentRecommendations,qualityContext,qualityCoverageReport,deliveryReadiness,activeDeliveryId].every(x=>x===null)'));
  check(run('[inboxDeliveries,deliveryFeedback,deliveryFollowUps,documents].every(x=>x.length===0)'));
  check(element('mockInboxContent').innerHTML === '' && element('deliveryReviewPanel').hidden);
  check(element('painCaseLabel').textContent === '等待分析' && element('careCaseLabel').textContent === '等待生成');
  // Late A response after B selection, for both direct clinical fetch paths.
  for (const method of ['analyzeCurrentPain', 'generateCurrentCare']) {
    seedA(); let release;
    context.fetch = async()=>await new Promise(resolve=>{release=resolve;});
    const pending = run(method+'()');
    run("resetCaseState('B')");
    release({ok:true,text:async()=>JSON.stringify({case_id:'A',summary:{},pain_points:[]})});
    await pending;
    check(run("result.case_id==='B' && painReport===null && careReport===null"));
  }
  for (const method of ['runPipeline','restructureFromCorrectedOcr']) {
    seedA();
    run("documents=[{id:'doc',name:'doc',page:1,text:'fixture',humanCorrected:true,ocrBlocks:[]}]");
    const payload=run("emptyCase('A')");
    let release;
    context.fetch=async()=>await new Promise(resolve=>{release=resolve;});
    const pending=run(method+'()');
    run("resetCaseState('B')");
    release({ok:true,json:async()=>payload,text:async()=>JSON.stringify(payload)});
    await pending;
    check(run("result.case_id==='B' && result.quality_flags.every(f=>f.code==='OCR_PENDING') && painReport===null && careReport===null"));
  }
  // Wrong case_id returned by the backend is rejected, even without a switch.
  seedA();run('painReport=null;careReport=null');
  context.fetch=async()=>({ok:true,text:async()=>JSON.stringify({case_id:'B',summary:{},pain_points:[]})});
  await run('analyzeCurrentPain()');
  check(run('painReport===null'));
  // Refresh metadata would otherwise overwrite A: also reject its mismatched ID.
  context.fetch=async()=>({ok:true,json:async()=>({case_id:'B'})});
  await assert.rejects(run('refreshWorkflow()'),/mismatch/);checks++;
  check(run("workflowCase.case_id==='A'"));
  seedA();run('clearDeliveryState()');
  context.fetch=async(url)=>({ok:true,json:async()=>url.endsWith('/A')?{case_id:'A'}:[{case_id:'B'}]});
  await run('loadDeliveryWorkflow()');
  check(run('inboxDeliveries.length===0 && deliveryFeedback.length===0 && deliveryFollowUps.length===0'));
  seedA();run('clearDeliveryState()');
  let releasePreparation;
  context.fetch=async()=>await new Promise(resolve=>{releasePreparation=resolve;});
  const oldPreparation=run('prepareCurrentDelivery()');
  run("resetCaseState('B');deliveryReadiness={case_id:'B',marker:'new'}");
  releasePreparation({ok:true,json:async()=>({case_id:'A'})});
  await oldPreparation;
  check(run("deliveryReadiness.case_id==='B' && deliveryReadiness.marker==='new'"));
  // A -> B -> A must still reject the old A response (generation, not just ID).
  let release;
  context.fetch=async()=>await new Promise(resolve=>{release=resolve;});
  const old=run("workflowFetch('/api/workflow/cases/A')");
  run("resetCaseState('B');resetCaseState('A')");
  release({ok:true,json:async()=>({case_id:'A'})});
  await assert.rejects(old,/案件已切换/);checks++;
  // Official UI metrics come only from the Python endpoint, never JS formulas.
  run("predictionData={case_id:'A'};goldData={case_id:'A'}");
  context.fetch=async(url,options)=>{
    assert.ok(url.endsWith('/api/evaluation/structured'));checks++;
    assert.deepEqual(JSON.parse(options.body),{prediction:{case_id:'A'},gold:{case_id:'A'}});checks++;
    return {ok:true,json:async()=>({micro_precision:.1,micro_recall:.2,micro_f1:.133,missing_rate:.3,evaluator_version:'test-python'})};
  };
  await run('runEvaluation()');
  check(element('evalF1').textContent==='13.3');
  check(element('evaluationDetails').textContent.includes('test-python'));
  context.fetch=async()=>({ok:true,json:async()=>({evaluation_status:'NOT_EVALUABLE',micro_f1:0,missing_rate:null})});
  await run('runEvaluation()');
  check(element('evalF1').textContent==='—' && element('evalMissing').textContent==='—');
  check(element('evaluationDetails').textContent.includes('Gold无可评测字段'));
  let releaseEvaluation;
  context.fetch=async()=>await new Promise(resolve=>{releaseEvaluation=resolve;});
  const oldEvaluation=run('runEvaluation()');
  run("goldData={case_id:'new-gold'}");
  releaseEvaluation({ok:true,json:async()=>({micro_precision:1,micro_recall:1,micro_f1:1,missing_rate:0,evaluator_version:'old-data'})});
  await oldEvaluation;
  check(element('evalF1').textContent==='—');
  // Recommendation failures share one Chinese prerequisite card instead of repeating backend messages.
  const sharedWarnings=['Disease group unavailable：无法可靠确定当前病种','Current provider context unconfirmed：请提供已确认医院来源上下文'];
  const sharedQuery={case_id:'A',disease_group:'unavailable',reports:['institution','clinician','treatment_plan'].map(entity_type=>({
    status:'insufficient_evidence',report:{entity_type,warnings:sharedWarnings,recommendations:[]}
  }))};
  const sharedHtml=run('renderRecommendationResults('+JSON.stringify(sharedQuery)+')');
  const sharedVisible=sharedHtml.split('<details class="technical-details recommendation-debug">')[0];
  check(!sharedVisible.includes('Disease group unavailable')&&!sharedVisible.includes('Current provider context unconfirmed'));
  check((sharedVisible.match(/当前病种尚未确认/g)||[]).length===1);
  check((sharedVisible.match(/当前医疗机构信息尚未确认/g)||[]).length===1);
  check((sharedVisible.match(/<h3>(医院|医生|治疗方案)推荐<\/h3>/g)||[]).length===3);
  check((sharedVisible.match(/暂不可用/g)||[]).length===4);
  check(sharedHtml.includes('查看技术详情')&&sharedHtml.includes('Disease group unavailable'));
  // Different causes stay with their respective modules and keep their precise Chinese meaning.
  const distinctQuery={case_id:'A',disease_group:'pneumonia',reports:[
    {status:'insufficient_evidence',report:{entity_type:'institution',warnings:['Insufficient historical data：0个同病种候选；每主体最低样本3'],recommendations:[]}},
    {status:'insufficient_evidence',report:{entity_type:'clinician',warnings:['Insufficient historical data：0个同病种候选；每主体最低样本3'],recommendations:[]}},
    {status:'insufficient_evidence',report:{entity_type:'treatment_plan',warnings:['Insufficient historical data：2个同病种候选；每主体最低样本3'],recommendations:[]}}
  ]};
  const distinctHtml=run('renderRecommendationResults('+JSON.stringify(distinctQuery)+')').split('<details class="technical-details recommendation-debug">')[0];
  check(distinctHtml.includes('当前病种无可比较机构'));
  check(distinctHtml.includes('医生身份信息不足'));
  check(distinctHtml.includes('治疗方案样本不足'));
  check(!distinctHtml.includes('当前数据不足，暂无法生成可靠推荐'));
  check(run("recommendationReasonKey('没有满足样本量、可计算维度和置信度门槛的候选，系统拒绝强行推荐','institution')") === 'recommendation_evidence_insufficient');
  // A real recommendation continues to render its result and traceable evidence details.
  const availableQuery={case_id:'A',disease_group:'pneumonia',reports:[
    {status:'available',report:{entity_type:'institution',disease_group:'pneumonia',warnings:[],recommendations:[{entity_id:'医院甲',composite_score:88,confidence:.9,sample_size:4,reasons:[{title:'专科证据',explanation:'已核验相关专科信息',evidence_fields:{source_ids:['official-1']}}],limitations:['仅供就医沟通参考']}]}},
    {status:'insufficient_evidence',report:{entity_type:'clinician',warnings:['Insufficient historical data：0个同病种候选；每主体最低样本3'],recommendations:[]}},
    {status:'insufficient_evidence',report:{entity_type:'treatment_plan',warnings:['Insufficient historical data：0个同病种候选；每主体最低样本3'],recommendations:[]}}
  ]};
  const availableHtml=run('renderRecommendationResults('+JSON.stringify(availableQuery)+')');
  check(availableHtml.includes('医院甲')&&availableHtml.includes('专科证据')&&availableHtml.includes('official-1'));

  // Phase 2 official fourth-question entry uses only V2 APIs and dynamic context.
  check(!source.includes('qualityV2SupportedDiseases'));
  check(source.includes('/api/quality/v2/recommendations/hospitals'));
  check(source.includes('/api/quality/v2/recommendations/clinicians'));
  check(source.includes('/api/quality/v2/treatment-evaluation'));
  check(source.includes('正在为当前病种发现并核验权威医疗资源'));
  check(source.includes('证据来源：')&&source.includes('证据摘要：')&&source.includes('查看原始页面：'));
  check(source.includes('建议核验来源：')&&source.includes('候选来源尚未完成后台核验'));
  check(source.includes('技术详情')&&source.includes('evidence_fields:'));
  check(htmlSource.includes('id="legacyQualityUi" hidden'));
  check(htmlSource.includes('仅处理已确认授权与脱敏的数据；推荐结果仅供就医参考'));
  check(styleSource.includes('@media(max-width:760px)')&&styleSource.includes('.quality-v2-context-grid,.quality-v2-treatment-grid{grid-template-columns:1fr}'));
  const phase2Calls=[];
  function v2HospitalReport(body) {
    const disease=phase1Catalog.diseases.find(item=>item.disease_group===body.context.disease_group);
    const sourceProfile=(disease||phase1Catalog.diseases[0]).hospitals[0];
    const profile=JSON.parse(JSON.stringify(sourceProfile));
    profile.disease_group=body.context.disease_group;
    profile.disease_specialty.disease_group=body.context.disease_group;
    const evidenceSource=profile.sources[0];
    profile.evidence_records=[{evidence_id:'EVD-FRONT-1',source_id:evidenceSource.source_id,source_name:evidenceSource.title,source_type:evidenceSource.authority_type,source_url:evidenceSource.url,query_date:evidenceSource.query_date,evidence_text:'权威原始页面中的必要能力短文本',evidence_field:'disease_specialty',supported_claim:'该资料支持当前病种相关专科能力。'}];
    return {context:body.context,strategy:'可信缓存优先：按真实规则计算',excluded:{},warnings:[],engine_version:'capability-recommendation-engine-v1',candidate_profiles:[profile],candidate_clinician_profiles:[],discovery_layers:[disease?'verified_benchmark_catalog':'fixture_authoritative_discovery'],dynamic_discovery_attempted:!disease,recommendations:[{
      rank:1,hospital_id:profile.hospital_id,hospital_name:profile.institution_foundation.hospital_name,
      recommendation_score:86,recommendation_confidence:.82,evidence_coverage:.85,evidence_status:'verified',
      accessibility:{patient_city:body.context.city,patient_province:body.context.province,hospital_city:profile.institution_foundation.city,hospital_province:profile.institution_foundation.province,same_city:body.context.city===profile.institution_foundation.city,same_province:false,nearby_major_city:false,distance_km:null,travel_level:'same_city',accessibility_level:'同城',assessment:{status:'evaluated',score:100,level:'same_city',confidence:.95,reasoning:'按真实地理规则计算',evidence_fields:['context.city'],source_ids:[],limitations:[]}},
      reasons:[
        {dimension:'disease_specialty',title:'病种专科匹配',explanation:'当前病种专科证据匹配',score:90,evidence_fields:['related_departments'],source_ids:[evidenceSource.source_id],evidence_record_ids:['EVD-FRONT-1']},
        {dimension:'geographic_accessibility',title:'就医可达性',explanation:'根据当前城市计算地理层级',score:100,evidence_fields:['context.city'],source_ids:[evidenceSource.source_id],evidence_record_ids:['EVD-FRONT-1']}
      ],dimension_scores:[
        {dimension:'disease_specialty_match',label:'病种/专科匹配',score:90,weight:.35,reasoning:'病种方向匹配',evidence_confidence:.9,evidence_status:'verified',source_ids:[evidenceSource.source_id],evidence_record_ids:['EVD-FRONT-1']},
        {dimension:'geographic_convenience',label:'地理与就医便利',score:100,weight:.2,reasoning:'同城就医',evidence_confidence:.9,evidence_status:'verified',source_ids:[evidenceSource.source_id],evidence_record_ids:['EVD-FRONT-1']},
        {dimension:'hospital_professional_capability',label:'医院专业能力',score:82,weight:.2,reasoning:'公开能力资料可核验',evidence_confidence:.8,evidence_status:'verified',source_ids:[evidenceSource.source_id],evidence_record_ids:['EVD-FRONT-1']},
        {dimension:'clinician_resources',label:'医生/专家资源',score:65,weight:.15,reasoning:'公开资料有限',evidence_confidence:.35,evidence_status:'pending_verification',source_ids:[],evidence_record_ids:[]},
        {dimension:'treatment_technology_capability',label:'技术与治疗能力',score:78,weight:.1,reasoning:'诊疗项目有公开资料',evidence_confidence:.75,evidence_status:'partially_verified',source_ids:[evidenceSource.source_id],evidence_record_ids:['EVD-FRONT-1']}
      ],evidence:['field:related_departments','field:context.city'],limitations:['历史案例数量有限']
    }]};
  }
  context.fetch=async(url,options={})=>{
    phase2Calls.push(url);
    if(url.endsWith('/api/quality/v2/recommendations/hospitals'))return response(v2HospitalReport(JSON.parse(options.body)));
    if(url.endsWith('/api/quality/v2/recommendations/clinicians'))return response([]);
    if(url.endsWith('/api/quality/v2/treatment-evaluation'))return response({
      case_id:'Q-CURRENT',pattern:{pattern_id:'internal-hash',disease_group:'pneumonia',readable_pattern_name:'抗感染与对症支持治疗',treatment_description:'根据本次结构化记录形成的治疗过程摘要',major_treatment:[],procedures:[],surgeries:[],core_medications:[],supportive_treatments:[],discharge_plan:[],evidence_fields:['diagnoses','medications']},
      treatment_diagnosis_match:{status:'evaluated',score:82,level:'matched',confidence:.8,reasoning:'现有治疗记录与主要诊断可以对应',evidence_fields:['diagnoses','medications'],source_ids:[],limitations:[]},
      medication_rationality:{status:'needs_review',score:null,level:null,confidence:.4,reasoning:'用药合理性需要专业人员结合完整记录复核',evidence_fields:['medications'],source_ids:[],limitations:['缺少完整用药背景']},
      procedure_rationality:{status:'not_applicable',score:null,level:null,confidence:.8,reasoning:'本次未记录操作或手术',evidence_fields:['procedures'],source_ids:[],limitations:[]},
      treatment_completeness:{status:'evaluated',score:75,level:'medium',confidence:.75,reasoning:'主要治疗环节已有记录',evidence_fields:['medications'],source_ids:[],limitations:[]},
      discharge_continuity:{status:'insufficient_evidence',score:null,level:null,confidence:.3,reasoning:'出院连续照护记录不足',evidence_fields:['events'],source_ids:[],limitations:['复诊信息不足']},
      risk_management:{status:'evaluated',score:70,level:'medium',confidence:.7,reasoning:'现有风险记录可供评价',evidence_fields:['quality_flags'],source_ids:[],limitations:[]},
      evidence_coverage:.7,overall_confidence:.68,key_findings:[],review_reasons:['用药需要复核'],historical_reference:{similar_historical_case_count:0,available:false,evidence_level:'none',average_fee:null,median_fee:null,average_length_of_stay:null,medication_observation:null,treatment_standardization_observation:null,readmission_observation:null,common_pain_points:[],risk_case_rate:null,evidence_coverage:0,source_ids:[],limitations:['暂无足够相似历史案例']},evaluation_version:'current-treatment-evaluation-v1'
    });
    throw Error('unexpected V2 URL '+url);
  };
  element('qualityV2ContextMode').value='demo';
  for(const preset of ['pneumonia|上海市|上海市','influenza|上海市|上海市','hypertension|江苏省|苏州市']){
    element('qualityV2DemoPreset').value=preset;
    await run('loadQualityV2()');
    check(element('qualityV2ContextBadge').textContent==='演示数据');
    check(element('qualityV2Hospitals').innerHTML.includes('当前推荐分'));
    check((element('qualityV2Hospitals').innerHTML.match(/quality-v2-reasons/g)||[]).length>=1);
    check(element('qualityV2Hospitals').innerHTML.includes('病种专科匹配')&&element('qualityV2Hospitals').innerHTML.includes('就医可达性'));
    check(element('qualityV2Hospitals').innerHTML.includes('固定五维评分')&&element('qualityV2Hospitals').innerHTML.includes('权重 35%'));
    check(element('qualityV2Hospitals').innerHTML.includes('公开资料有限')&&element('qualityV2Hospitals').innerHTML.includes('证据可信度'));
    check(element('qualityV2Hospitals').innerHTML.includes('证据来源：')&&element('qualityV2Hospitals').innerHTML.includes('证据摘要：'));
    check(element('qualityV2Hospitals').innerHTML.includes('权威原始页面中的必要能力短文本')&&element('qualityV2Hospitals').innerHTML.includes('查看原始页面：'));
    check(element('qualityV2Hospitals').innerHTML.includes('核验状态：')&&element('qualityV2Hospitals').innerHTML.includes('已核验'));
    check(!element('qualityV2Hospitals').innerHTML.includes('对应字段：'));
    check(element('qualityV2Hospitals').innerHTML.includes('技术详情')&&element('qualityV2Hospitals').innerHTML.includes('evidence_fields:'));
    check(element('qualityV2Treatment').innerHTML.includes('演示上下文不生成治疗评价'));
  }
  check(phase2Calls.some(url=>url.endsWith('/api/quality/v2/recommendations/hospitals')));
  check(phase2Calls.some(url=>url.endsWith('/api/quality/v2/recommendations/clinicians')));
  check(!phase2Calls.some(url=>url.includes('/api/quality/recommendations')));

  // ConnectTimeout degrades evidence status but never hides the five structured model candidates.
  const pendingReport=v2HospitalReport({context:{case_id:'TIMEOUT',disease_group:'cataract',disease_label:'白内障',province:'测试省',city:'测试市'}});
  const pendingBase=pendingReport.recommendations[0];
  pendingReport.warnings=['联网检索失败：ConnectTimeout','部分候选的联网公开证据尚未完成核验'];
  pendingReport.recommendations=Array.from({length:5},(_,index)=>({...pendingBase,rank:index+1,hospital_id:'PENDING-'+index,hospital_name:'待核验医院'+(index+1),evidence_status:'pending_verification',recommendation_confidence:.05,reasons:pendingBase.reasons.map(reason=>({...reason,evidence_status:'pending_verification',evidence_record_ids:[],source_ids:[],suggested_source_name:'测试医院官网',suggested_source_url:'https://hospital.example.cn/pending',suggested_evidence_summary:'核验该院是否设有与当前病种相关的科室',explanation:'推荐基于当前病例与模型综合判断，公开证据暂未完成核验。'})),dimension_scores:pendingBase.dimension_scores.map(row=>({...row,evidence_status:'pending_verification',evidence_confidence:0,evidence_record_ids:[],source_ids:[]}))}));
  context.pendingReport=pendingReport;
  run('qualityV2HospitalReport=pendingReport;renderQualityV2Hospitals()');
  check((element('qualityV2Hospitals').innerHTML.match(/quality-v2-hospital-card/g)||[]).length===5);
  check((element('qualityV2Hospitals').innerHTML.match(/待核验/g)||[]).length>=5);
  check(element('qualityV2Hospitals').innerHTML.includes('联网检索失败：ConnectTimeout'));
  check(element('qualityV2Hospitals').innerHTML.includes('建议核验来源：')&&element('qualityV2Hospitals').innerHTML.includes('测试医院官网'));
  check(element('qualityV2Hospitals').innerHTML.includes('建议核验内容：')&&element('qualityV2Hospitals').innerHTML.includes('候选来源链接：'));
  check(element('qualityV2Hospitals').innerHTML.includes('候选来源尚未完成后台核验，不作为已核验证据。'));
  check(!element('qualityV2Hospitals').innerHTML.includes('0 家通过证据门槛'));

  // A reliable disease outside the benchmark catalog still enters the generic discovery path.
  run("workflowCase={case_id:'Q-CURRENT',has_structured_result:true};result=emptyCase('Q-CURRENT');result.diagnoses=[{diagnosis_id:'D1',name:makeField('糖尿病',.9,null)}];qualityV2CurrentCity='上海市';");
  element('qualityV2ContextMode').value='current';
  await run('loadQualityV2()');
  check(run("qualityV2Context().disease_group==='diabetes'&&qualityV2Context().disease_label==='糖尿病'"));
  check(element('qualityV2Hospitals').innerHTML.includes('权威')||element('qualityV2Hospitals').innerHTML.includes('当前推荐分'));
  check(!element('qualityV2Status').innerHTML.includes('正式候选目录'));
  check(phase2Calls.some(url=>url.endsWith('/api/quality/v2/recommendations/hospitals')));
  // Low-confidence diagnoses are not promoted to recommendation facts.
  run("careReport={case_id:'Q-CURRENT',guide:{disease_topic:'白内障'}};result.diagnoses=[{diagnosis_id:'D1',name:makeField('屈光不正',.95,null)}]");
  check(run("qualityV2Context().disease_group==='cataract'&&qualityV2Context().disease_label==='白内障'&&qualityV2DiseaseInfoFromCase().source==='care_guide.disease_topic'"));
  run("careReport=null");
  run("result.diagnoses=[{diagnosis_id:'D1',name:makeField('白内障',.4,null)}];qualityV2CurrentCity='上海市';");
  await run('loadQualityV2()');
  check(element('qualityV2Status').innerHTML.includes('尚未可靠识别明确病种'));
  // Missing city remains explicit; users cannot override the disease itself.
  run("result.diagnoses=[{diagnosis_id:'D1',name:makeField('肺炎',.9,null)}];qualityV2CurrentCity='';");
  await run('loadQualityV2()');
  check(element('qualityV2Status').innerHTML.includes('尚未提供患者所在地'));

  // A current case calls treatment V2, renders all six dimensions, cautious summary and no fake history.
  run("qualityV2CurrentCity='上海市';painReport={case_id:'Q-CURRENT',pain_points:[]};");
  await run('loadQualityV2()');
  check(phase2Calls.some(url=>url.endsWith('/api/quality/v2/treatment-evaluation')));
  check((element('qualityV2Treatment').innerHTML.match(/quality-v2-treatment-item/g)||[]).length===6);
  check(element('qualityV2Treatment').innerHTML.includes('建议人工复核'));
  check(element('qualityV2Treatment').innerHTML.includes('给患者的一句话总结'));
  check(element('qualityV2Treatment').innerHTML.includes('暂无足够相似历史案例'));
  check(!element('qualityV2Treatment').innerHTML.includes('最佳治疗方案')&&!element('qualityV2Treatment').innerHTML.includes('internal-hash'));
  check((element('qualityV2HospitalProfile').innerHTML.match(/quality-v2-dimension/g)||[]).length===7);
  check(element('qualityV2HospitalProfile').innerHTML.includes('当前推荐情境'));
  check(element('qualityV2Clinicians').innerHTML.includes('当前缺少足够可追溯医生资料'));
  const clinicianAssessment={status:'evaluated',score:80,level:'matched',confidence:.8,reasoning:'公开资料与当前病例匹配',evidence_fields:['official_field'],source_ids:['DOCTOR-1'],limitations:[]};
  const noHistory={historical_case_count:0,disease_specific_case_count:0,available_history:false,evidence_level:'none',history_summary:'暂无可关联历史案例',supported_metrics:[],evidence_coverage:0,source_ids:[],assessment:{status:'insufficient_evidence',score:null,level:null,confidence:0,reasoning:'没有历史病例',evidence_fields:[],source_ids:[],limitations:[]}};
  context.phase2ClinicianProfile={clinician_id:'DOC-1',disease_group:'pneumonia',basic_qualification:{clinician_id:'DOC-1',clinician_name:'测试医生',hospital_id:'H1',hospital_name:'测试医院',department:'呼吸科',title:'主治医师',professional_role:null,assessment:clinicianAssessment},disease_expertise:{disease_group:'pneumonia',disease_expertise:['肺炎诊疗'],disease_specific_experience_description:null,specialty_match_level:'matched',assessment:clinicianAssessment},treatment_expertise:{surgery_expertise:[],intervention_expertise:[],treatment_expertise:['感染诊疗'],complex_case_expertise:[],special_population_expertise:[],assessment:clinicianAssessment},platform_capability:{hospital_level:'三级',department_strength:'呼吸专科平台',key_specialty_support:null,multidisciplinary_support:null,critical_care_support:null,advanced_technology_support:[],assessment:clinicianAssessment},current_case_match:{disease_match:'匹配',comorbidity_match:null,treatment_need_match:null,complexity_match:null,overall_case_match:'匹配',assessment:clinicianAssessment},historical_evidence:noHistory,sources:[]};
  run("qualityV2ClinicianProfiles=[phase2ClinicianProfile];qualityV2ClinicianRecommendations=[{rank:1,clinician_id:'DOC-1',clinician_name:'测试医生',hospital_id:'H1',recommendation_score:85,recommendation_confidence:.8,evidence_status:'pending_verification',reasons:[{title:'病种匹配',explanation:'与当前肺炎病例匹配'},{title:'平台能力',explanation:'所属科室能力有来源'}],dimension_scores:[{dimension:'disease_match',label:'当前病种匹配',score:88,weight:.35,reasoning:'方向匹配',evidence_confidence:.8,evidence_status:'pending_verification'}],limitations:['暂无历史病例']}];renderQualityV2Clinicians()");
  check((element('qualityV2Clinicians').innerHTML.match(/quality-v2-dimension/g)||[]).length===6);
  check(element('qualityV2Clinicians').innerHTML.includes('暂无可关联历史案例')&&!element('qualityV2Clinicians').innerHTML.includes('表现较差'));
  check(element('qualityV2Clinicians').innerHTML.includes('固定四维评分')&&element('qualityV2Clinicians').innerHTML.includes('权重 35%'));
  check(element('qualityV2Clinicians').innerHTML.includes('待核验'));

  // Demo mode never mutates the real case and leaving it restores current-case facts.
  const realCaseBefore=run('result.case_id');
  element('qualityV2ContextMode').value='demo';element('qualityV2ContextMode').listeners.change();
  check(run('result.case_id')===realCaseBefore&&run("qualityV2Context().mode==='demo'"));
  element('qualityV2ContextMode').value='current';element('qualityV2ContextMode').listeners.change();
  check(run('result.case_id')===realCaseBefore&&run("qualityV2Context().disease_group==='pneumonia'"));
  run("resetCaseState('Q-NEXT')");
  check(run('qualityV2HospitalReport===null&&qualityV2TreatmentEvaluation===null&&qualityV2SelectedHospitalId===null'));
  check(!source.includes('主任 > 副主任')&&!source.includes('.sort((a,b)=>a.title'));
  return {checks, scope:'Mock DOM/API regression; not browser E2E'};
}
module.exports = {runFrontendHardening};
if (require.main === module) runFrontendHardening().then(result=>console.log(JSON.stringify(result))).catch(error=>{console.error(error);process.exitCode=1;});
