(async function(){
  const form=document.getElementById('loginForm');const error=document.getElementById('loginError');const button=document.getElementById('loginButton');
  async function request(path,options={}){const response=await fetch(path,{credentials:'same-origin',...options});const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.detail||'请求失败');return body;}
  try{const me=await request('/api/auth/me');if(me.role_code==='CLAIM')location.replace('/claim');}catch(_ignored){}
  form.addEventListener('submit',async(event)=>{event.preventDefault();error.textContent='';button.disabled=true;button.textContent='正在登录…';try{const user=await request('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('username').value.trim(),password:document.getElementById('password').value})});if(user.role_code!=='CLAIM'){await request('/api/auth/logout',{method:'POST'});throw new Error('当前版本尚未开放该角色工作区');}location.replace('/claim');}catch(exc){error.textContent=exc.message;}finally{button.disabled=false;button.textContent='登录';}});
})();
