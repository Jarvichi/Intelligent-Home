'use strict';
(()=>{const status=document.querySelector('#status'),picture=document.querySelector('#frame'),button=document.querySelector('#refresh');let busy=false;
 async function refresh(){if(busy)return;busy=true;button.disabled=true;try{let response=await fetch('api/refresh',{method:'POST'});if(!response.ok)throw Error();let data=await response.json();if(data.has_frame){picture.hidden=false;picture.src='api/frame?updated='+Date.now();status.textContent=data.stale?'Source unavailable. Showing the last picture received.':'Picture updated.';}else{picture.hidden=true;status.textContent=data.state==='not_configured'?'Add a picture source in the app configuration.':'Picture unavailable. Check the source and try again.';}}catch(e){status.textContent='Could not check the picture. Try again.';}finally{button.disabled=false;busy=false;}}
 button.addEventListener('click',refresh);refresh();setInterval(()=>{if(!document.hidden)refresh();},60000);
})();
