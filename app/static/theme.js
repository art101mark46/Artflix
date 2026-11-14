(() => {
 const toggle=document.getElementById('theme-toggle');
 const preference=window.matchMedia('(prefers-color-scheme: dark)');
 let saved=null;
 try { saved=localStorage.getItem('movie-metadata-theme'); } catch {}
 function apply(dark){
  document.documentElement.dataset.theme=dark?'dark':'light';
  document.documentElement.style.colorScheme=dark?'dark':'light';
  toggle.textContent=dark?'☀':'☾';
  toggle.title=dark?'Switch to light mode':'Switch to dark mode';
  toggle.setAttribute('aria-label',toggle.title);
  toggle.setAttribute('aria-pressed',String(dark));
 }
 apply(saved==='dark'||(saved!=='light'&&preference.matches));
 toggle.addEventListener('click',()=>{
  const dark=document.documentElement.dataset.theme!=='dark';
  apply(dark);saved=dark?'dark':'light';
  try { localStorage.setItem('movie-metadata-theme',saved); } catch {}
 });
 const follow=e=>{if(!saved)apply(e.matches)};
 if(preference.addEventListener)preference.addEventListener('change',follow);
 else if(preference.addListener)preference.addListener(follow);
})();
