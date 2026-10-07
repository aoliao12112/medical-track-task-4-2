// Run with: node tests/frontend_hardening.test.cjs
// Mock DOM regression, explicitly not a real-browser E2E test.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

async function runFrontendHardening(root = path.resolve(__dirname, '..')) {
  const source = fs.readFileSync(path.join(root, 'web/dist/app.js'), 'utf8');
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
      inboxDeliveries=[{case_id:'A'}];deliveryFeedback=[{case_id:'A'}];deliveryFollowUps=[{case_id:'A'}];activeDeliveryId='old';`);
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
  return {checks, scope:'Mock DOM/API regression; not browser E2E'};
}
module.exports = {runFrontendHardening};
if (require.main === module) runFrontendHardening().then(result=>console.log(JSON.stringify(result))).catch(error=>{console.error(error);process.exitCode=1;});
