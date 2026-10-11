(async function(){
  const form=document.getElementById('loginForm');const error=document.getElementById('loginError');const button=document.getElementById('loginButton');
  const workspaces={CLAIM:'/claim',MEDICAL:'/medical',CUSTOMER:'/customer',SERVICE:'/service'};
  async function request(path,options={}){const response=await fetch(path,{credentials:'same-origin',...options});const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.detail||'请求失败');return body;}
  try{const me=await request('/api/auth/me');if(workspaces[me.role_code])location.replace(workspaces[me.role_code]);}catch(_ignored){}
  form.addEventListener('submit',async(event)=>{event.preventDefault();error.textContent='';button.disabled=true;button.textContent='正在登录…';try{const user=await request('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('username').value.trim(),password:document.getElementById('password').value})});if(!workspaces[user.role_code]){await request('/api/auth/logout',{method:'POST'});throw new Error('当前角色工作区尚未开放');}location.replace(workspaces[user.role_code]);}catch(exc){error.textContent=exc.message;}finally{button.disabled=false;button.textContent='登录';}});
})();
