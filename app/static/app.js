const $ = (s) => document.querySelector(s);
let editToken=null;
let healthFilter=null;
let duplicateGroups=new Map();
let scanPollTimer=null;
let selectionVersion=0;
let libraryRoot='';
let libraryReadOnly=false;
let tvSortPrefixes=['The','A','An'];
let sortPrefixes=['The','A','An'];
let setExact=false;
let visibleMovieRows=[];
let movies = [], current = null, chosenMovie = null, artworkItems = [], inspectData = null;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(url, options={}) {
  const r = await fetch(url, {headers:{"Content-Type":"application/json",...(editToken?{"X-Artflix-Edit-Token":editToken}:{})}, ...options});
  const d = await r.json().catch(()=>({detail:r.statusText}));
  if (!r.ok) {if(r.status===423){editToken=null;applyEditState()}throw new Error(d.detail || "Request failed");}
  return d;
}
function prettyDuration(seconds) {
  if (!seconds) return "unknown duration";
  const mins = Math.floor(seconds/60);
  return `${Math.floor(mins/60)}h ${mins%60}m`;
}
async function reloadLibrary(){
 const d=await api('/api/library');movies=d.movies;libraryRoot=d.root||libraryRoot;refreshFilterSuggestions();renderMovies();
}
async function scan(){
 $('#scan').disabled=true;
 try{await api('/api/scan/start',{method:'POST'});pollScan()}catch(e){setLibraryStatus(e.message);$('#scan').disabled=false}
}
function setLibraryStatus(message){const status=$('#library-status');status.textContent=message;status.hidden=!message;}
function renderMovies() {
  refreshHealthDashboard();
  visibleMovieRows=movies.map((movie,index)=>({movie,index})).filter(row=>matchesLibraryFilters(row.movie)).sort((a,b)=>compareMovies(a.movie,b.movie));
  $('#movies-heading').textContent=`Movies (${visibleMovieRows.length})`;
  $('#movie-list').innerHTML=visibleMovieRows.length?visibleMovieRows.map(({movie:m,index:i})=>`<button class="movie-item ${movieCompleteness(m).status} ${current?.folder===m.folder?'active':''}" title="${esc(movieCompleteness(m).label)}" aria-label="${esc(m.name+'. '+movieCompleteness(m).label)}" data-index="${i}">${!m.has_nfo?'<span class="new-star" title="New movie">★</span>':''}${esc(m.name)}${missingMovieSummary(m)}</button>`).join(''):movies.length?'<p class="muted">No movies match these filters.</p>':'<p class="muted">No movie files found. Check the configured scan path.</p>';
  document.querySelectorAll('.movie-item').forEach(b=>b.onclick=()=>selectMovie(movies[+b.dataset.index]));
  updateAlphabet();
}
// The file-details section is moved out of #chosen before rebuilding the tabs.
// Reset its entire contents so generated controls cannot follow a different movie.
function resetMovieFilePanel(){
 const info=$('.file-info');if(!info)return;
 info.innerHTML='<h3>Current folder and video details</h3><div id="existing-metadata"></div><div id="video-metadata"></div>';
}
async function selectMovie(m) {
  const version=++selectionVersion;
  resetMovieFilePanel();
  resetLibraryFilters();
  $("#search-form").hidden=false;
  showWorkspaceTab("movie");
  current=m; chosenMovie=null; inspectData=null;
  try{localStorage.setItem('artflix-last-movie',JSON.stringify({root:libraryRoot,folder:m.folder}))}catch{}
  if($(".file-info")) $("#chosen").before($(".file-info"));
  $('#empty').hidden=true; $('#review').hidden=false; $('#selected-name').textContent=m.name;
  $('#video-files').textContent=m.videos.join(' · '); $('#results').innerHTML=''; $('#chosen').hidden=true;
  $('#existing-metadata').innerHTML='<p class="muted">Reading existing metadata…</p>';
  $('#video-metadata').innerHTML='';
  $('#query').value=m.name.replace(/[._]/g,' ').replace(/\s+/g,' ').trim().replace(/\s+(1080p|2160p|720p|bluray|web|hdtv|x264|x265|hevc|proper|repack).*$/i,'');
  $('#year').value=(m.name.match(/\b(19|20)\d{2}\b/)||[])[0]||'';
  renderMovies();
  try {
    const inspected=await api('/api/inspect',{method:'POST',body:JSON.stringify({folder:m.folder})});
    if(version!==selectionVersion)return;
    inspectData=inspected;
    const old=inspectData.metadata||{};
    if (old.title) $('#query').value=old.title;
    if (old.year) $('#year').value=old.year;
    $('#existing-metadata').innerHTML=old.error?`<p class="warning">${esc(old.error)}</p>`:old.title?`<dl class="existing-grid"><dt>Title</dt><dd>${esc(old.title)}</dd><dt>Original title</dt><dd>${esc(old.originaltitle||'')}</dd><dt>Year</dt><dd>${esc(old.year||'')}</dd><dt>Certificate</dt><dd>${esc(old.mpaa||'not set')}</dd><dt>TMDB ID</dt><dd>${esc(old.tmdbid||'not linked')}</dd><dt>Plot</dt><dd>${esc(old.plot||'')}</dd></dl>`:'<p class="muted">No existing movie.nfo. Search to match this folder.</p>';
    $('#video-metadata').innerHTML=inspectData.videos.map(v=>`<article class="media-info"><strong>${esc(v.name)}</strong><span>${prettyDuration(v.duration)} · ${v.size_bytes?`${(v.size_bytes/1024**3).toFixed(2)} GB`:''}</span><span>${v.video?.map(x=>`${esc(x.width||'?')}×${esc(x.height||'?')} ${esc(x.codec)}`).join(', ')||'Video details unavailable'}</span><span>${v.audio?.length?'Audio: '+v.audio.map(x=>`${esc(x.name)}${x.language?' ('+esc(x.language)+')':''}`).join(', '):'No audio details'}</span><span>${v.subtitles?.length?`${v.subtitles.length} subtitle track(s)`:''}</span>${v.probe_note?`<small>${esc(v.probe_note)}</small>`:''}</article>`).join('')||'<p class="muted">No video files found.</p>';
    await review(Number(old.tmdbid)||0,false,true);
  } catch(e) { if(version===selectionVersion)$('#existing-metadata').innerHTML=`<p class="warning">${esc(e.message)}</p>`; }
}
$('#scan').onclick=scan;
$('#search-form').onsubmit=async e=>{
  e.preventDefault(); $('#results').innerHTML='<p class="muted">Searching…</p>';
  try {
    const d=await api('/api/search',{method:'POST',body:JSON.stringify({query:$('#query').value,year:$('#year').value?Number($('#year').value):null})});
    $('#results').innerHTML=d.results.length?d.results.map(m=>`<article class="result"><img src="${m.poster_path?'https://image.tmdb.org/t/p/w92'+esc(m.poster_path):''}" onerror="this.style.visibility='hidden'" alt=""><div><h3>${esc(m.title)} ${m.release_date?`(${esc(m.release_date.slice(0,4))})`:''}</h3><p>${esc((m.overview||'No description').slice(0,180))}</p></div><button data-id="${m.id}">Review</button></article>`).join(''):'<p class="muted">No results. Try a different title or year.</p>';
    document.querySelectorAll('.result button').forEach(b=>b.onclick=()=>review(+b.dataset.id));
  } catch(err) { $('#results').innerHTML=`<p class="warning">${esc(err.message)}</p>`; }
};
function certificateInput(m) {
  const common = {US:['G','PG','PG-13','R','NC-17'],GB:['U','PG','12A','12','15','18','R18'],AU:['G','PG','M','MA15+','R18+'],CA:['G','PG','14A','18A','R'],NZ:['G','PG','M','R13','R16','R18']}[m.region]||['G','PG','12','15','18','R','NC-17','Not Rated','Unrated'];
  const options=[...new Set([...(m.certifications||[]),...common])];
  return `<label for="cert-choice">Certificate ${m.region?`(${esc(m.region)})`:''}</label><input id="cert-choice" list="certificate-options" value="${esc(m.certification||'')}" placeholder="Choose or type a certificate"><datalist id="certificate-options">${options.map(c=>`<option value="${esc(c)}">`).join('')}</datalist><small>You can enter a custom certificate if TMDB has no rating listed.</small>`;
}
function filenamePreview() {
  if (!chosenMovie || !inspectData) return '';
  const pattern=inspectData.filename_format||'{title} ({year})';
  const values={title:$('#edit-title')?.value||'',original_title:$('#edit-original-title')?.value||'',year:$('#edit-year')?.value||'',tmdb_id:String(chosenMovie.details.id)};
  const choices=document.querySelectorAll('.primary-video');
  const selected=new Set([...choices].filter(box=>box.checked).map(box=>box.dataset.video));
  const videos=(inspectData.videos||[]).filter(video=>!choices.length||selected.has(video.name));
  const output=videos.map((v,i)=>{
    let stem=pattern.replace(/\{(title|original_title|year|tmdb_id)\}/g,(_,key)=>values[key]).replace(/[<>:"/\\|?*\x00-\x1f]/g,' ').replace(/\s+/g,' ').trim().replace(/[ .]+$/g,'')||'movie';
    if(videos.length>1) stem+=` - ${String(i+1).padStart(2,'0')}`;
    return `${v.name} → ${stem}${v.name.slice(v.name.lastIndexOf('.'))}`;
  });
  return output.length?`<strong>Filename preview</strong>${output.map(x=>`<div>${esc(x)}</div>`).join('')}`:'No videos to rename.';
}
async function review(id, fresh=false, local=false) {
  const version=selectionVersion;
  try {
    const [m,a]=local?[inspectData.local.movie,inspectData.local.artwork]:await Promise.all([api(`/api/movie/${id}`),api(`/api/artwork/${id}`)]);
    if(version!==selectionVersion)return false;
    chosenMovie=m; artworkItems=a.artwork;
    const d=m.details, old=inspectData?.metadata||{}, reuse=!fresh&&String(old.tmdbid||'')===String(id);
    const val=(key, fallback)=>reuse&&Object.hasOwn(old,key)?old[key]:fallback;
    $('#chosen').hidden=false;
    if($('.file-info')) $('#chosen').before($('.file-info'));
    const savedMetadata=$('#existing-metadata').innerHTML;
    resetMovieFilePanel();
    $('#existing-metadata').innerHTML=savedMetadata;
    $('#chosen').innerHTML=`<div class="chosen-head">${d.poster_url||d.poster_path?`<img src="${esc(d.poster_url||('https://image.tmdb.org/t/p/w185'+d.poster_path))}" alt="Movie poster">`:''}<div><h3>${esc(d.title)} (${esc((d.release_date||'').slice(0,4))})</h3><p>${esc(d.overview||'No overview returned.')}</p><p class="muted">TMDB ID ${d.id?esc(d.id):'not linked'} · ${esc(d.runtime||'')} min</p></div></div>
      <section class="edit-fields"><h3>Metadata to save</h3><div class="edit-grid"><label>Title<input id="edit-title" value="${esc(val('title',d.title||''))}"></label><label>Original title<input id="edit-original-title" value="${esc(val('originaltitle',d.original_title||''))}"></label><label>Year<input id="edit-year" value="${esc(val('year',(d.release_date||'').slice(0,4)))}"></label><label>Release date<input id="edit-premiered" value="${esc(val('premiered',d.release_date||''))}" placeholder="YYYY-MM-DD"></label><label>Runtime (minutes)<input id="edit-runtime" value="${esc(val('runtime',d.runtime||''))}"></label><label>Genres, comma separated<input id="edit-genres" value="${esc(val('genres',(d.genres||[]).map(g=>g.name).join(', ')))}"></label><label>Tagline<input id="edit-tagline" value="${esc(val('tagline',d.tagline||''))}"></label><label>Directors, comma separated<input id="edit-directors" value="${esc(val('directors',(d.credits?.crew||[]).filter(x=>x.job==='Director').map(x=>x.name).join(', ')))}"></label><label class="wide">Studios, comma separated<input id="edit-studios" value="${esc(val('studios',(d.production_companies||[]).map(x=>x.name).join(', ')))}"></label><label class="wide">Plot<textarea id="edit-plot" rows="4">${esc(val('plot',d.overview||''))}</textarea></label></div>${certificateInput({...m,certification:val('mpaa',m.certification)})}</section>
      <section class="rename-area"><h3>Rename video files</h3><label class="check"><input id="rename-videos" type="checkbox"> Rename video files when approving metadata</label><small>Uses the movie filename format from Settings.</small><div id="rename-preview" class="preview-list"></div></section>
      <section class="actors-area"><h3>Actor portraits</h3><p class="muted">${local?'Saved cast and portraits. Use refresh movie to choose new TMDB portraits.':'Choose TMDB cast portraits to save under this movie’s actors folder.'}</p><div class="actors-list">${(d.credits?.cast||[]).slice(0,30).map(person=>`<label class="actor"><input type="checkbox" data-actor="${person.id}" ${!person.profile_path?'disabled':''} ${person.profile_path&&person.order<10?'checked':''}>${person.local_photo||person.profile_path?`<img src="${esc(person.local_photo||('https://image.tmdb.org/t/p/w92'+person.profile_path))}" alt="">`:'<span class="no-portrait">No portrait</span>'}<span>${esc(person.name)}<small>${esc(person.character||'')}</small></span></label>`).join('')||'<p class="muted">No cast information returned.</p>'}</div></section>
      <section class="art-area"><h3>${local?'Saved artwork':'Choose movie artwork'}</h3>${a.notice?`<p class="muted">${esc(a.notice)}</p>`:''}<p class="muted">Click any image to inspect it full size.</p><div class="artwork">${artworkItems.map((x,i)=>`<div class="art"><button type="button" class="art-preview" data-preview="${i}" aria-label="Preview ${esc(x.name)}"><img src="${esc(x.url)}" alt="${esc(x.type)}"></button><label><input type="checkbox" data-art="${i}"> ${esc(x.name)} · ${esc(x.provider||'Saved')} ${x.lang?`(${esc(x.lang)})`:''}</label></div>`).join('')||'<p class="muted">No artwork available.</p>'}</div></section>
      <p class="warning">Review the proposed files and tick the replacements you want before saving.</p><button class="approve" id="approve">Review and save changes</button> <span id="write-status" class="muted"></span>`;
    arrangeReview();
    arrangeMovieTabs();
    installActorSelectionControls();
    enhanceAssetDetails();
    addSetAndMissingFields();addMediaRoles();addCollectionControls();addArtworkRefresh();isolateVideoDetails();
    installArtworkSelectionControls();addBackupControls();addDuplicateNotice();document.querySelectorAll('[data-actor]').forEach(input=>input.addEventListener('change',applyEditState));applyEditState();
    $('#rename-preview').innerHTML=filenamePreview();
    for(const selector of ['#edit-title','#edit-original-title','#edit-year']) $(selector).oninput=()=>$('#rename-preview').innerHTML=filenamePreview();
    bindMovieArtworkSelection();document.querySelectorAll('.art-preview').forEach(b=>b.onclick=()=>{
      openArtwork(Number(b.dataset.preview));
    });
    $('#approve').onclick=()=>write();
    return true;
  } catch(e) { if(!fresh)window.alert(e.message);return false; }
}
async function write(actorsOnly=false) {
  const art={}, actor_ids=[];
  document.querySelectorAll('[data-art]:checked').forEach(c=>{const item=artworkItems[+c.dataset.art];art[item.name]=item.url});
  document.querySelectorAll('[data-actor]:checked:not(:disabled)').forEach(c=>actor_ids.push(Number(c.dataset.actor)));
  const metadata={title:$('#edit-title').value,original_title:$('#edit-original-title').value,year:$('#edit-year').value,premiered:$('#edit-premiered').value,runtime:$('#edit-runtime').value,genres:$('#edit-genres').value,tagline:$('#edit-tagline').value,directors:$('#edit-directors').value,studios:$('#edit-studios').value,set:$('#edit-set').value,writers:$('#edit-writers').value,plot:$('#edit-plot').value};
  $('#approve').disabled=true;$('#write-status').textContent=' Saving…';
  try {
    const d=await reviewedWrite({folder:current.folder,tmdb_id:Number($('#edit-tmdb-id').value)||chosenMovie.details.id,artwork:art,certification:$('#cert-choice').value,metadata,actor_ids,actors_only:actorsOnly,rename_videos:!actorsOnly&&$('#rename-videos').checked,primary_videos:$('#rename-videos').checked?[...document.querySelectorAll('.primary-video:checked')].map(box=>box.dataset.video):null,filename_format:inspectData.filename_format,replace_existing:true,folder_name:$('#rename-folder').checked?$('#folder-name').value:null});
    if(!d){$('#write-status').textContent='';return;}
    const folder=d.folder;
    await reloadLibrary();
    const refreshed=movies.find(m=>m.folder===folder);
    if(refreshed) {
      await selectMovie(refreshed);
      $('#write-status').textContent='Saved: '+d.written.join(', ');
    }
  } catch(e) { $('#write-status').textContent=' '+e.message; }
  finally { applyEditState(); }
}
const dialog=$('#settings-dialog');
let settingsTemplateDraft={};
async function openSettings() {
  try {
    const d=await api('/api/settings'),f=$('#settings-form');settingsTemplateDraft={...(await api('/api/tv/naming-templates')).templates};renderSettingsTemplates();
    for(const k of ['root_path','write_path','language','region','filename_format','tv_root_path','tv_write_path','tv_language','tv_filename_format','tv_multi_episode_filename_format'])f.elements[k].value=d[k]||'';
    f.elements.sort_ignore_prefixes.value=(d.sort_ignore_prefixes||[]).join(', ');f.elements.tv_sort_ignore_prefixes.value=(d.tv_sort_ignore_prefixes||d.sort_ignore_prefixes||[]).join(', ');
    f.elements.tvdb_key.value='';f.elements.tvdb_pin.value='';f.elements.overwrite.checked=!!d.overwrite;f.elements.tmdb_token.value='';f.elements.fanart_key.value='';f.elements.fanart_client_key.value='';
    $('#credential-state').textContent=`TMDB token ${d.has_tmdb_token?'saved':'not set'} · fanart.tv key ${d.has_fanart_key?'saved':'not set'} · TVDB key ${d.has_tvdb_key?'saved':'not set'}`;$('#settings-message').textContent='';applyEditState();dialog.showModal();
  } catch(e) { window.alert(e.message); }
}
$('#settings-open').onclick=openSettings;$('#settings-close').onclick=()=>dialog.close();$('#cancel-settings').onclick=()=>dialog.close();
$('#settings-form').onsubmit=async e=>{
  e.preventDefault();const f=e.currentTarget;
  const body={tv_root_path:f.elements.tv_root_path.value,tv_write_path:f.elements.tv_write_path.value,tv_language:f.elements.tv_language.value||'eng',tv_filename_format:f.elements.tv_filename_format.value||'{show} - S{season}E{episode} - {title}',tv_multi_episode_filename_format:f.elements.tv_multi_episode_filename_format.value||'{show} - S{season}E{episode} - {title}',tv_sort_ignore_prefixes:f.elements.tv_sort_ignore_prefixes.value.split(',').map(p=>p.trim()).filter(Boolean),tvdb_key:f.elements.tvdb_key.value,tvdb_pin:f.elements.tvdb_pin.value,root_path:f.elements.root_path.value,write_path:f.elements.write_path.value,language:f.elements.language.value||'en-US',region:f.elements.region.value.toUpperCase(),filename_format:f.elements.filename_format.value||'{title} ({year})',tmdb_token:f.elements.tmdb_token.value,fanart_key:f.elements.fanart_key.value,fanart_client_key:f.elements.fanart_client_key.value,overwrite:f.elements.overwrite.checked,sort_ignore_prefixes:f.elements.sort_ignore_prefixes.value.split(',').map(p=>p.trim()).filter(Boolean)};
  body.tv_naming_templates=settingsTemplateDraft;
  try { await api('/api/settings',{method:'PUT',body:JSON.stringify(body)});if(inspectData){inspectData.filename_format=body.filename_format;if($('#rename-preview'))$('#rename-preview').innerHTML=filenamePreview()}if(typeof tvCurrent!=='undefined'&&tvCurrent){tvCurrent.filename_format=body.tv_filename_format;tvCurrent.multi_episode_filename_format=body.tv_multi_episode_filename_format;if($('#tv-rename-example'))scheduleTVRenameExample()}sortPrefixes=body.sort_ignore_prefixes;tvSortPrefixes=body.tv_sort_ignore_prefixes;renderMovies();$('#settings-message').textContent='Settings saved.';setTimeout(()=>dialog.close(),450); }
  catch(err) { $('#settings-message').textContent=err.message; }
};
$('#image-close').onclick=()=>$('#image-dialog').close();$('#image-dialog').onclick=e=>{if(e.target===$('#image-dialog'))$('#image-dialog').close()};

const libraryFilters={};
function filterValue(m,key){
 const d=m.searchable||{},media=m.media||[];
 if(key==='title')return d.title||m.name;
 if(key==='year')return d.year||m.name;
 if(key==='certificate')return d.mpaa||'';
 if(key==='set')return d.set||'';
 if(key==='genres'||key==='actors')return (d[key]||[]).join(' ');
 if(key==='subtitles')return media.some(v=>v.subtitles?.length)?'yes':'no';
 if(key==='resolution')return media.flatMap(v=>v.video||[]).map(v=>`${v.width}x${v.height} ${v.height>=2000?'4k 2160p':v.height>=1000?'1080p':v.height>=700?'720p':''}`).join(' ');
 return media.flatMap(v=>v[key==='audio'?'audio':'video']||[]).map(v=>v.codec+' '+v.name).join(' ');
}
const filterPanel=document.createElement('section');
filterPanel.id='library-search-panel';filterPanel.className='library-search';filterPanel.setAttribute('role','tabpanel');
filterPanel.innerHTML='<h2>Search your library</h2><p class="muted">Combine filters to narrow the movies in the left column. Select a result to open its details.</p><div class="filter-grid">'+['title','year','certificate','actors','genres','set','resolution','audio','codec','subtitles'].map(k=>`<label>${k}<input data-filter="${k}" list="filter-options-${k}" autocomplete="off" placeholder="${k==='subtitles'?'yes / no':k}"><datalist id="filter-options-${k}"></datalist></label>`).join('')+'<button id="clear-filters" type="button">Clear filters</button></div><p id="filter-count" role="status"></p>';
const work=$('.work'),moviePanel=document.createElement('div');moviePanel.id='movie-panel';moviePanel.setAttribute('role','tabpanel');
while(work.firstChild)moviePanel.append(work.firstChild);
const workspaceTabs=document.createElement('div');workspaceTabs.className='workspace-tabs';workspaceTabs.setAttribute('role','tablist');workspaceTabs.setAttribute('aria-label','Library workspace');
workspaceTabs.innerHTML='<button type="button" id="movie-tab" role="tab" aria-controls="movie-panel">Movie details</button><button type="button" id="search-tab" role="tab" aria-controls="library-search-panel">Search library</button>';
moviePanel.setAttribute('aria-labelledby','movie-tab');filterPanel.setAttribute('aria-labelledby','search-tab');
work.append(workspaceTabs,moviePanel,filterPanel);
function showWorkspaceTab(tab){const movie=tab==='movie';moviePanel.hidden=!movie;filterPanel.hidden=movie;$('#movie-tab').setAttribute('aria-selected',String(movie));$('#search-tab').setAttribute('aria-selected',String(!movie));}
$('#movie-tab').onclick=()=>showWorkspaceTab('movie');$('#search-tab').onclick=()=>showWorkspaceTab('search');
workspaceTabs.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const search=e.key==='End'||(e.key!=='Home'&&document.activeElement===$('#movie-tab'));showWorkspaceTab(search?'search':'movie');$(search?'#search-tab':'#movie-tab').focus()}};
filterPanel.querySelectorAll('[data-filter]').forEach(input=>input.oninput=()=>{if(input.dataset.filter==='set')setExact=false;libraryFilters[input.dataset.filter]=input.value;renderMovies();updateFilterCount()});
function resetLibraryFilters(){healthFilter=null;setExact=false;filterPanel.querySelectorAll('[data-filter]').forEach(i=>i.value='');filterPanel.querySelectorAll('[data-missing]').forEach(i=>i.checked=false);missingSelection.clear();$('#missing-mode').value='any';Object.keys(libraryFilters).forEach(k=>delete libraryFilters[k]);renderMovies();updateFilterCount()}
$('#clear-filters').onclick=resetLibraryFilters;
function updateFilterCount(){const count=movies.filter(m=>matchesLibraryFilters(m)).length;$('#filter-count').textContent=`${count} of ${movies.length} movies match`;}
function refreshFilterSuggestions(){
 filterPanel.querySelectorAll('[data-filter]').forEach(input=>{
  const key=input.dataset.filter,values=[];
  movies.forEach(m=>{const d=m.searchable||{},media=m.media||[];
   if(key==='title')values.push(d.title||m.name);
   else if(key==='year')values.push(d.year||((m.name.match(/\b(?:19|20)\d{2}\b/)||[])[0]||''));
   else if(key==='certificate')values.push(d.mpaa||'');
   else if(key==='set')values.push(d.set||'');
   else if(key==='genres'||key==='actors')values.push(...(d[key]||[]));
   else if(key==='subtitles')values.push(media.some(v=>v.subtitles?.length)?'yes':'no');
   else media.forEach(v=>(v[key==='audio'?'audio':'video']||[]).forEach(stream=>{if(key==='resolution'){if(stream.width&&stream.height)values.push(`${stream.width}x${stream.height}`);if(stream.height>=2000)values.push('4k','2160p');else if(stream.height>=1000)values.push('1080p');else if(stream.height>=700)values.push('720p')}else values.push(stream.codec||'',stream.name||'')}));
  });
  const unique=[...new Map(values.filter(Boolean).map(v=>[String(v).toLowerCase(),String(v)])).values()].sort((a,b)=>a.localeCompare(b));
  $(`#filter-options-${key}`).innerHTML=unique.map(v=>`<option value="${esc(v)}"></option>`).join('');
 });updateFilterCount();
}
showWorkspaceTab('movie');
function arrangeReview(){
 const chosen=$('#chosen'), info=$('.file-info');
 chosen.prepend(info);info.before($('.chosen-head'),$('.edit-fields'));
 const rename=$('.rename-area');chosen.append(rename,chosen.querySelector(':scope > .warning'),$('#approve'),$('#write-status'));
 rename.insertAdjacentHTML('beforeend',`<label class="check"><input type="checkbox" id="rename-folder"> Rename movie folder</label><label>Folder name<input id="folder-name" value="${esc(current.name)}"></label><small>Use the button to fill from the video format, then adjust the name before saving.</small><button type="button" id="folder-from-format">Use filename format</button>`);
 $('#folder-from-format').onclick=()=>{const values={title:$('#edit-title').value,original_title:$('#edit-original-title').value,year:$('#edit-year').value,tmdb_id:String(chosenMovie.details.id)};$('#folder-name').value=inspectData.filename_format.replace(/\{(title|original_title|year|tmdb_id)\}/g,(_,k)=>values[k]).replace(/[<>:"/\\|?*]/g,' ').trim();$('#rename-folder').checked=true};
 const tabs=document.createElement('div');tabs.className='asset-tabs';tabs.innerHTML='<div role="tablist"><button type="button" role="tab" aria-selected="true">Actor portraits</button><button type="button" role="tab" aria-selected="false">Fanart artwork</button></div>';
 const actors=$('.actors-area'),art=$('.art-area');actors.before(tabs);tabs.append(actors,art);art.hidden=true;
 tabs.querySelectorAll('[role=tab]').forEach((b,i)=>b.onclick=()=>{actors.hidden=i!==0;art.hidden=i!==1;tabs.querySelectorAll('[role=tab]').forEach((x,j)=>x.setAttribute('aria-selected',String(i===j)))});
 const input=$('#edit-genres');input.type='hidden';const holder=document.createElement('div');holder.className='genre-editor';input.after(holder);
 const remembered=JSON.parse(localStorage.getItem('movie-genres')||'[]');const known=[...new Set([...remembered,...movies.flatMap(m=>m.searchable?.genres||[]),...input.value.split(',').map(x=>x.trim()).filter(Boolean)])];
 let tags=input.value.split(',').map(x=>x.trim()).filter(Boolean);
 const render=()=>{input.value=tags.join(', ');holder.innerHTML=tags.map((t,i)=>`<button type="button" class="genre-tag" data-remove="${i}">${esc(t)} ×</button>`).join('')+'<input id="genre-add" list="genre-suggestions" placeholder="Add genre and press Enter"><datalist id="genre-suggestions">'+known.map(t=>`<option value="${esc(t)}">`).join('')+'</datalist>';holder.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{tags.splice(+b.dataset.remove,1);render()});$('#genre-add').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();const tag=e.target.value.trim();if(tag&&!tags.some(t=>t.toLowerCase()===tag.toLowerCase())){tags.push(tag);if(!known.includes(tag))known.push(tag);localStorage.setItem('movie-genres',JSON.stringify(known))}render()}}};render();input.closest('label').firstChild.textContent='Genres';
}


// Search is an overlay within the workspace, preserving the selected movie.
workspaceTabs.hidden=true;
const folderHeading=$('aside h2');folderHeading.classList.add('folder-heading');
const librarySearchButton=document.createElement('button');librarySearchButton.type='button';librarySearchButton.className='icon-button';librarySearchButton.title='Search library';librarySearchButton.setAttribute('aria-label','Search library');librarySearchButton.innerHTML='<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>';folderHeading.append(librarySearchButton);librarySearchButton.onclick=()=>showWorkspaceTab('search');
const closeSearch=document.createElement('button');closeSearch.type='button';closeSearch.className='icon-button search-close';closeSearch.textContent='×';closeSearch.title='Close search';closeSearch.setAttribute('aria-label','Close search');closeSearch.onclick=()=>showWorkspaceTab('movie');filterPanel.prepend(closeSearch);
async function refreshMovie(){
 const id=Number(inspectData?.metadata?.tmdbid||chosenMovie?.details?.id);
 if(id&&await review(id,true))return;
 const title=$('#edit-title')?.value||inspectData?.metadata?.title||$('#query').value;
 const year=$('#edit-year')?.value||inspectData?.metadata?.year||$('#year').value;
 $('#query').value=title;$('#year').value=year;$('#search-form').hidden=false;$('#search-form').requestSubmit();
}
function arrangeMovieTabs(){
 const chosen=$('#chosen'),head=$('.chosen-head'),details=$('.edit-fields'),info=$('.file-info'),rename=$('.rename-area'),actors=$('.actors-area'),art=$('.art-area');
 const oldAssetTabs=$('.asset-tabs');if(oldAssetTabs){oldAssetTabs.before(actors,art);oldAssetTabs.remove()}
 const nav=document.createElement('div');nav.className='movie-tabs';nav.setAttribute('role','tablist');
 const panels=[document.createElement('section'),document.createElement('section'),document.createElement('section'),document.createElement('section')];
 const labels=['Movie Details','Actors','Artwork','Metadata'];
 chosen.prepend(nav);panels.forEach((panel,i)=>{panel.className='movie-tab-panel';panel.setAttribute('role','tabpanel');panel.id='movie-content-'+i;chosen.append(panel);const b=document.createElement('button');b.type='button';b.textContent=labels[i];b.setAttribute('role','tab');b.setAttribute('aria-controls',panel.id);b.onclick=()=>{panels.forEach((p,j)=>p.hidden=i!==j);nav.querySelectorAll('button').forEach((x,j)=>x.setAttribute('aria-selected',String(i===j)))};nav.append(b)});
 panels[0].append(head,details);panels[3].append(info,rename);panels[1].append(actors);panels[2].append(art);actors.hidden=false;art.hidden=false;
 const refresh=document.createElement('button');refresh.type='button';refresh.className='icon-button refresh-movie';refresh.textContent='↻';refresh.title='refresh movie';refresh.setAttribute('aria-label','refresh movie');refresh.onclick=refreshMovie;panels[0].prepend(refresh);
 info.querySelectorAll('.movie-path').forEach(p=>p.remove());
 info.insertAdjacentHTML('afterbegin',`<p class="movie-path"><strong>Folder path:</strong> ${esc(inspectData?.folder_path||current.folder)}</p>`);
 info.insertAdjacentHTML('beforeend',`<div class="movie-path">${Object.values(inspectData?.file_paths||{}).map(p=>`<div><strong>File path:</strong> ${esc(p)}</div>`).join('')}<p><strong>External subtitles:</strong> ${esc((inspectData?.sidecars||[]).join(', ')||'None')}</p></div>`);
 const saveActors=document.createElement('button');saveActors.type='button';saveActors.textContent='Save selected actor portraits';saveActors.onclick=()=>write(true);actors.append(saveActors);
 const actions=document.createElement('div');actions.className='movie-save-bar';actions.append($('#approve'),$('#write-status'));chosen.append(actions);
 const warning=chosen.querySelector(':scope > .warning');if(warning)rename.append(warning);
 $('#search-form').hidden=true;$('#results').innerHTML='';nav.querySelector('button').click();
}

function enhanceAssetDetails(){
 installMovieExtras();
 const fields=$('.edit-fields'),cert=$('#cert-choice'),certLabel=fields.querySelector('label[for="cert-choice"]');
 const row=document.createElement('div');row.className='certificate-row';const left=document.createElement('div');left.append(certLabel,cert);row.append(left);row.insertAdjacentHTML('beforeend',`<label>TMDB ID<input id="edit-tmdb-id" type="number" min="1" value="${chosenMovie.details.id||''}"></label>`);fields.append(row);$('#edit-tmdb-id').onchange=()=>{const id=Number($('#edit-tmdb-id').value);if(Number.isInteger(id)&&id>0&&id!==chosenMovie.details.id)review(id,true)};
 const writers=inspectData?.metadata?.writers||(chosenMovie.details.credits?.crew||[]).filter(p=>['Writer','Screenplay','Story'].includes(p.job)).map(p=>p.name).filter((p,i,a)=>a.indexOf(p)===i).join(', ');
 $('.edit-grid').insertAdjacentHTML('beforeend',`<label class="wide">Writer/s<input id="edit-writers" value="${esc(writers)}"></label>`);
 $('#existing-metadata').hidden=true;$('.file-info h3').textContent='Video details';
 const saved=inspectData?.saved_assets||{};
 document.querySelectorAll('[data-actor]').forEach(box=>{const downloaded=(saved.actor_ids||[]).includes(Number(box.dataset.actor))||!!chosenMovie.details.credits?.cast?.find(person=>person.id===Number(box.dataset.actor))?.local_photo;box.checked=false;if(downloaded){box.closest('.actor').classList.add('downloaded');box.closest('.actor').insertAdjacentHTML('beforeend','<small class="download-label">Saved</small>')}});
 const grid=$('.artwork'),groups=new Map();
 const headings={movieposter:'Posters',moviethumb:'Landscapes',moviebackground:'Backgrounds',moviedisc:'Discs',hdmovieclearart:'Clearart',hdmovielogo:'Logos'};
 artworkItems.forEach((item,i)=>{let group=groups.get(item.type);if(!group){group=document.createElement('section');group.className='artwork-category';group.innerHTML=`<h4>${esc(headings[item.type]||item.type)}</h4><div class="artwork-grid"></div>`;groups.set(item.type,group)}const card=grid.querySelector(`[data-art="${i}"]`).closest('.art');if(item.downloaded||saved.artwork?.[item.name]===item.url){card.classList.add('downloaded');card.insertAdjacentHTML('beforeend','<small class="download-label">Saved</small>')}group.querySelector('.artwork-grid').append(card)});
 grid.replaceChildren(...groups.values());
 $('#video-metadata').innerHTML=(inspectData?.videos||[]).map(v=>`<article class="media-info"><strong>${esc(v.name)}</strong><span>File size: ${(v.size_bytes/1024**3).toFixed(2)} GB</span><span>Running time: ${prettyDuration(v.duration)}</span>${(v.video||[]).map(x=>`<span>Resolution: ${esc(x.width)} × ${esc(x.height)}</span><span>Video codec: ${esc(x.codec)}</span><span>Display aspect ratio: ${esc(x.aspect_ratio||'Unknown')}</span><span>Scan type: ${esc(x.scan_type||'Unknown')}</span>`).join('')}<strong>Audio</strong>${(v.audio||[]).map(x=>`<span>Audio codec: ${esc(x.codec)}</span><span>Channels: ${esc(x.channels||'Unknown')}</span><span>Language: ${esc(languageName(x.language))}</span>`).join('')||'<span>None reported</span>'}<strong>Included subtitles</strong>${(v.subtitles||[]).map(x=>`<span>${esc(x.codec)} · Language: ${esc(languageName(x.language))}</span>`).join('')||'<span>None reported</span>'}</article>`).join('');
 $('#video-metadata').insertAdjacentHTML('beforeend','<small>Display aspect ratio describes the encoded frame, including any black bars.</small>');
}

function isolateVideoDetails(){
 const articles=[...document.querySelectorAll('#video-metadata .media-info')];
 if(articles.length<2)return;
 const selector=document.createElement('label');selector.className='metadata-file-selector';selector.innerHTML='Video file to inspect<select aria-label="Video file to inspect">'+(inspectData.videos||[]).map((v,i)=>`<option value="${i}">${esc(v.name)}</option>`).join('')+'</select><small>This folder contains multiple video candidates. Details below describe only the selected file.</small>';
 const show=index=>{articles.forEach((article,i)=>article.hidden=i!==index);document.querySelectorAll('.file-info .movie-path>div').forEach((row,i)=>row.hidden=i!==index)};
 $('#video-metadata').prepend(selector);selector.querySelector('select').onchange=e=>show(Number(e.target.value));show(0);
}
function renderArtworkChoices(){
 const headings={movieposter:'Posters',moviethumb:'Landscapes',moviebackground:'Backgrounds',moviedisc:'Discs',hdmovieclearart:'Clearart',hdmovielogo:'Logos'};
 const groups=new Map();artworkItems.forEach((item,i)=>{if(!groups.has(item.type))groups.set(item.type,[]);groups.get(item.type).push({item,i})});
 $('.artwork').innerHTML=[...groups].map(([type,entries])=>`<section class="artwork-category"><h4>${esc(headings[type]||type)}</h4><div class="artwork-grid">${entries.map(({item:x,i})=>{const saved=x.local||x.downloaded||inspectData?.saved_assets?.artwork?.[x.name]===x.url;return `<div class="art ${saved?'downloaded':''}"><button type="button" class="art-preview" data-preview="${i}" aria-label="Preview ${esc(x.name)}"><img src="${esc(x.url)}" alt="${esc(x.type)}"></button><label><input type="checkbox" data-art="${i}" ${x.local?'disabled':''}>${esc(x.name)} · ${esc(x.provider||'Saved')} ${esc(x.lang||'')}</label>${saved?'<small class="download-label">Saved</small>':''}</div>`}).join('')}</div></section>`).join('')||'<p class="muted">No artwork available.</p>';
 bindMovieArtworkSelection();document.querySelectorAll('.art-preview').forEach(button=>button.onclick=()=>openArtwork(Number(button.dataset.preview)));applyEditState();
}
function addArtworkRefresh(){
 const button=document.createElement('button');button.type='button';button.textContent='Refresh artwork from all providers';button.className='refresh-artwork secondary';$('.art-area').prepend(button);
 const status=document.createElement('p');status.className='muted';status.setAttribute('role','status');button.after(status);
 button.onclick=async()=>{const version=selectionVersion,id=Number($('#edit-tmdb-id')?.value||chosenMovie?.details?.id);if(!id){status.textContent='Match this movie or enter its TMDB ID first.';return}button.disabled=true;status.textContent='Fetching artwork…';try{const result=await api('/api/artwork/'+id);if(version!==selectionVersion)return;const local=artworkItems.filter(item=>item.local);const seen=new Set(local.map(item=>item.url));artworkItems=[...local,...result.artwork.filter(item=>!seen.has(item.url))];renderArtworkChoices();status.textContent=result.notice||`Loaded ${result.artwork.length} provider images. Saved artwork remains highlighted.`}catch(e){status.textContent=e.message}finally{button.disabled=false}};
}
let activeArtwork=0;
function openArtwork(index){
 activeArtwork=index;const item=artworkItems[index];if(!item)return;
 $('#full-image').src=item.url;$('#image-caption').textContent=`${item.name} · ${item.type} · ${item.provider||'Saved'}${item.lang?' · '+item.lang:''}${inspectData?.saved_assets?.artwork?.[item.name]===item.url?' · Saved':''}`;
 $('#image-download').textContent=item.local?'Already saved':'Save image to movie folder';
 $('#image-download').disabled=item.local||libraryReadOnly||!editToken;
 if(!$('#image-dialog').open)$('#image-dialog').showModal();
}
function moveArtwork(direction){const indices=artworkItems.map((a,i)=>a.type===artworkItems[activeArtwork]?.type?i:-1).filter(i=>i>=0);if(!indices.length)return;openArtwork(indices[(indices.indexOf(activeArtwork)+direction+indices.length)%indices.length])}
const imageControls=document.createElement('div');imageControls.className='image-controls';imageControls.innerHTML='<button type="button" id="image-prev" aria-label="Previous image">❮</button><button type="button" id="image-download">Save image to movie folder</button><button type="button" id="image-next" aria-label="Next image">❯</button><span id="image-status" role="status"></span>';$('#image-dialog').append(imageControls);
$('#image-prev').onclick=()=>moveArtwork(-1);$('#image-next').onclick=()=>moveArtwork(1);
$('#image-dialog').addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();moveArtwork(e.key==='ArrowLeft'?-1:1)}});
$('#image-download').onclick=async()=>{
 const item=artworkItems[activeArtwork],button=$('#image-download');button.disabled=true;
 try{const saved=await reviewedWrite({folder:current.folder,tmdb_id:chosenMovie.details.id,artwork:{[item.name]:item.url},artwork_only:true,rename_videos:false,replace_existing:true});if(!saved)return;await reloadLibrary();current=movies.find(m=>m.folder===current.folder)||current;
 inspectData.saved_assets ||= {};inspectData.saved_assets.artwork ||= {};inspectData.saved_assets.artwork[item.name]=item.url;
 document.querySelectorAll('[data-art]').forEach(input=>{const a=artworkItems[Number(input.dataset.art)];if(a.name===item.name){const card=input.closest('.art');card.classList.toggle('downloaded',a.url===item.url);card.querySelector('.download-label')?.remove();if(a.url===item.url)card.insertAdjacentHTML('beforeend','<small class="download-label">Saved</small>')}});openArtwork(activeArtwork);$('#image-status').textContent='Saved to '+item.name;
 }catch(e){$('#image-status').textContent=e.message}finally{applyEditState()}
};
function installMovieExtras(){
 const badge=document.createElement('div');badge.id='certificate-badge';$('.chosen-head').append(badge);
 const update=()=>{const rating=$('#cert-choice').value.trim().toUpperCase();badge.replaceChildren();if(!rating)return;const label=document.createElement('span');label.textContent='Certificate: '+rating;badge.append(label);if(['U','PG','12','12A','15','18','R18'].includes(rating)&&(!chosenMovie.region||chosenMovie.region==='GB')){const img=document.createElement('img');img.src=['U','PG','12','12A','15','18'].includes(rating)?'/static/bbfc/BBFC-'+rating+'.png':'/api/bbfc/'+encodeURIComponent(rating);img.dataset.rating=rating;img.alt='BBFC '+rating;img.onerror=()=>img.remove();badge.prepend(img)}};$('#cert-choice').addEventListener('input',update);update();
 const reset=document.createElement('button');reset.type='button';reset.className='reset-metadata';reset.textContent='Clear stored metadata for this movie';$('.rename-area').append(reset);
 reset.onclick=async()=>{try{const plan=await api('/api/reset',{method:'POST',body:JSON.stringify({folder:current.folder})});if(!window.confirm('Delete metadata and images for '+current.name+'? Videos and subtitles will be preserved.\n\n'+plan.files.join('\n')))return;await api('/api/reset',{method:'POST',body:JSON.stringify({folder:current.folder,confirm:true,expected_files:plan.files})});const folder=current.folder;await reloadLibrary();await selectMovie(movies.find(m=>m.folder===folder)||current)}catch(e){window.alert(e.message)}};
}

function languageName(code){
 if(!code||code==='und')return 'Unknown';
 const iso={eng:'en',fra:'fr',fre:'fr',deu:'de',ger:'de',spa:'es',ita:'it',jpn:'ja',zho:'zh',chi:'zh',kor:'ko',por:'pt',rus:'ru',ara:'ar',hin:'hi',nld:'nl',dut:'nl'};
 try{return new Intl.DisplayNames(['en'],{type:'language'}).of(iso[code]||code)||code}catch{return code}
}

async function restoreWorkspace(){
 try{
  const [library,settings]=await Promise.all([api('/api/library'),api('/api/settings')]);
  movies=library.movies;libraryRoot=library.root;libraryReadOnly=!!settings.read_only;sortPrefixes=settings.sort_ignore_prefixes||[];
  if(settings.read_only){const label=document.createElement('span');label.className='read-only-label';label.textContent='Read only';label.title='Saving, renaming and reset are disabled by the server';$('.header-actions').prepend(label)}
  setLibraryStatus(movies.length?'':'Click Scan to discover movies.');
  refreshFilterSuggestions();renderMovies();
  let last=null;try{last=JSON.parse(localStorage.getItem('artflix-last-movie')||'null')}catch{}
  if(last?.root===libraryRoot&&(typeof libraryMode==='undefined'||libraryMode==='movies')){const movie=movies.find(m=>m.folder===last.folder);if(movie)await selectMovie(movie)}
  await loadSavedSearches();await loadSystemSummary();applyEditState();
  const job=await api('/api/scan/status');if(['running','cancelling'].includes(job.state))pollScan();
 }catch(e){setLibraryStatus('Unable to restore saved library: '+e.message)}
}

const missingSelection=new Set();
const missingLabels={nfo:'NFO file (missing or unreadable)',title:'Title',original_title:'Original title',tmdb_id:'TMDB ID',year:'Year',release_date:'Release date',runtime:'Runtime',genres:'Genres',tagline:'Tagline',directors:'Director/s',writers:'Writer/s',studios:'Studio/s',plot:'Plot',certificate:'Certificate',actors:'Cast names',poster:'Poster',fanart:'Background artwork',actor_portraits:'Actor portraits (none downloaded)',subtitles:'Subtitles (embedded or external)'};
const missingPanel=document.createElement('fieldset');missingPanel.className='missing-fields';missingPanel.innerHTML='<legend>Find missing fields</legend><p class="muted">Select the fields to check. Blank values count as missing. Optional items such as subtitles are checked only when you select them.</p><label>Match <select id="missing-mode"><option value="any">Any selected field is missing</option><option value="all">All selected fields are missing</option></select></label><div class="missing-grid">'+Object.entries(missingLabels).map(([key,label])=>`<label class="check"><input type="checkbox" data-missing="${key}">${esc(label)}</label>`).join('')+'</div>';
$('#filter-count').before(missingPanel);
function matchesLibraryFilters(movie){
 if(healthFilter&&!matchesHealth(movie,healthFilter))return false;
 if(!Object.entries(libraryFilters).every(([key,value])=>!value||(key==='set'&&setExact?filterValue(movie,key).toLowerCase()===value.toLowerCase():filterValue(movie,key).toLowerCase().includes(value.toLowerCase()))))return false;
 if(!missingSelection.size)return true;
 if(!Array.isArray(movie.missing_fields))return false;
 const matches=field=>movie.missing_fields.includes(field);
 return $('#missing-mode').value==='all'?[...missingSelection].every(matches):[...missingSelection].some(matches);
}
missingPanel.querySelectorAll('[data-missing]').forEach(box=>box.onchange=()=>{if(box.checked)missingSelection.add(box.dataset.missing);else missingSelection.delete(box.dataset.missing);renderMovies();updateFilterCount()});
$('#missing-mode').onchange=()=>{renderMovies();updateFilterCount()};


function movieCompleteness(movie){
 const optional=new Set(['tagline','actor_portraits','subtitles']);
 const missing=(movie.missing_fields||[]).filter(field=>!optional.has(field));
 const status=movie.metadata_status||(!movie.has_nfo?'none':missing.length?'partial':'complete');
 return {status:status==='none'?'metadata-none':status==='partial'?'metadata-partial':'metadata-complete',label:status==='none'?'No readable movie metadata':status==='partial'?'Missing: '+missing.map(field=>missingLabels[field]||field).join(', '):'Metadata complete'};
}

function sortTitle(movie,prefixes=sortPrefixes){
 let title=(movie.searchable?.title||movie.name||'').trim();
 const prefix=[...prefixes].sort((a,b)=>b.length-a.length).find(p=>title.toLocaleLowerCase().startsWith(p.toLocaleLowerCase()+' '));
 if(prefix)title=title.slice(prefix.length).trimStart();
 return title;
}
const movieCollator=new Intl.Collator(undefined,{numeric:true,sensitivity:'base'});
function compareMovies(a,b){return movieCollator.compare(sortTitle(a),sortTitle(b))||movieCollator.compare(a.name,b.name)}
function alphabetLetter(movie){const first=sortTitle(movie).normalize('NFKD').replace(/\p{M}/gu,'').charAt(0).toUpperCase();return /^[A-Z]$/.test(first)?first:'#'}
const movieBrowser=document.createElement('div');movieBrowser.className='movie-browser';$('#movie-list').before(movieBrowser);
const alphabet=document.createElement('nav');alphabet.className='alphabet-nav';alphabet.setAttribute('aria-label','Jump to a movie letter');
alphabet.innerHTML=['#',...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'].map(letter=>`<button type="button" data-letter="${letter}" title="Jump to ${letter} (using title sorting settings)" aria-label="Jump to ${letter}">${letter}</button>`).join('');movieBrowser.append(alphabet,$('#movie-list'));
function updateAlphabet(){
 alphabet.querySelectorAll('button').forEach(button=>{button.disabled=!visibleMovieRows.some(row=>alphabetLetter(row.movie)===button.dataset.letter)});
}
alphabet.querySelectorAll('button').forEach(button=>button.onclick=()=>{
 const row=visibleMovieRows.find(row=>alphabetLetter(row.movie)===button.dataset.letter);if(!row)return;
 const entry=$(`#movie-list [data-index="${row.index}"]`);entry?.scrollIntoView({behavior:'smooth',block:'nearest'});entry?.focus({preventScroll:true});
});
function missingMovieSummary(movie){
 if(movieCompleteness(movie).status!=='metadata-partial')return '';
 const fields=(movie.missing_fields||[]).filter(f=>!['tagline','actor_portraits','subtitles'].includes(f));
 return `<small class="missing-entry-summary">Missing: ${esc(fields.slice(0,2).map(f=>missingLabels[f]||f).join(', '))}${fields.length>2?` (+${fields.length-2})`:''}</small>`;
}
function addSetAndMissingFields(){
 const old=inspectData?.metadata||{},d=chosenMovie.details;
 const sameMovie=String(old.tmdbid||'')===String(d.id);
 const name=sameMovie&&Object.hasOwn(old,'set')?old.set:d.belongs_to_collection?.name||'';
 const known=[...new Set([...movies.map(m=>m.searchable?.set).filter(Boolean),...[name,d.belongs_to_collection?.name].filter(Boolean)])].sort((a,b)=>a.localeCompare(b));
 $('.edit-grid').insertAdjacentHTML('beforeend',`<label class="wide">Movie set<div class="set-editor"><input id="edit-set" list="movie-set-suggestions" value="${esc(name)}" placeholder="Choose or enter a movie set"><button id="show-movie-set" type="button" title="Show movies in this set from your scanned library">Show movies in set</button></div><datalist id="movie-set-suggestions">${known.map(s=>`<option value="${esc(s)}"></option>`).join('')}</datalist></label>`);
 const button=$('#show-movie-set'),input=$('#edit-set');const enable=()=>button.disabled=!input.value.trim();input.oninput=enable;enable();
 button.onclick=()=>{const set=input.value.trim();resetLibraryFilters();libraryFilters.set=set;setExact=true;filterPanel.querySelector('[data-filter="set"]').value=set;renderMovies();updateFilterCount();showWorkspaceTab('search')};
 const missing=inspectData?.missing_fields||current.missing_fields||[];
 if(missing.length){const core=missing.filter(f=>!['tagline','actor_portraits','subtitles'].includes(f));const optional=missing.filter(f=>['tagline','actor_portraits','subtitles'].includes(f));
 const report=document.createElement('div');report.className='missing-report';report.innerHTML=`${core.length?'<strong>Missing from saved metadata</strong><div class="missing-chips">'+core.map(f=>`<span>${esc(missingLabels[f]||f)}</span>`).join('')+'</div>':'<strong>Core saved metadata is complete</strong>'}${optional.length?'<details><summary>Optional items not present</summary>'+optional.map(f=>`<div>${esc(missingLabels[f]||f)}</div>`).join('')+'</details>':''}`;$('.edit-fields').prepend(report);
 const ids={title:'edit-title',original_title:'edit-original-title',tmdb_id:'edit-tmdb-id',year:'edit-year',release_date:'edit-premiered',runtime:'edit-runtime',genres:'edit-genres',directors:'edit-directors',writers:'edit-writers',studios:'edit-studios',plot:'edit-plot',certificate:'cert-choice'};
 core.forEach(field=>{const el=document.getElementById(ids[field]);if(el){const label=el.closest('label')||el;label.classList.add('field-missing');label.title='Missing from saved metadata; review and save to fill this field'}});
 }
}

// The padlock is an explicit accidental-edit guard. Its token is not persisted.
const editLockButton=document.createElement('button');editLockButton.id='edit-lock';editLockButton.type='button';editLockButton.className='secondary icon-button';$('.header-actions').append(editLockButton);
const settingsUnlock=document.createElement('button');settingsUnlock.type='button';settingsUnlock.className='secondary';settingsUnlock.textContent='Unlock settings';$('.settings-tabs').before(settingsUnlock);
async function toggleEditing(){
 editLockButton.disabled=true;
 try{const state=await api('/api/edit-lock',{method:'POST',body:JSON.stringify({locked:!!editToken})});editToken=state.token||null;applyEditState()}catch(e){window.alert(e.message)}finally{editLockButton.disabled=false}
}
editLockButton.onclick=toggleEditing;settingsUnlock.onclick=toggleEditing;
function applyEditState(){
 const locked=!editToken,blocked=locked||libraryReadOnly;
 editLockButton.textContent=locked?'🔒':'🔓';editLockButton.title=locked?'Unlock editing':'Lock editing';editLockButton.setAttribute('aria-label',editLockButton.title);editLockButton.setAttribute('aria-pressed',String(!locked));
 settingsUnlock.hidden=!locked;document.querySelectorAll('#settings-template-use,#settings-template-add').forEach(button=>button.disabled=locked);$('#settings-form').querySelectorAll('input').forEach(input=>input.disabled=locked);$('#settings-form .actions button:last-child').disabled=locked;
 document.querySelectorAll('.edit-fields input,.edit-fields textarea').forEach(input=>input.readOnly=blocked);
 document.querySelectorAll('.edit-fields .genre-tag,#folder-from-format,.rename-area input').forEach(el=>el.disabled=blocked);
 document.querySelectorAll('#approve,.reset-metadata,.actors-area>button,.restore-backup').forEach(button=>{button.disabled=blocked;button.title=libraryReadOnly?'Server read-only mode':locked?'Unlock editing to make changes':''});
 document.querySelectorAll('[data-actor]').forEach(input=>{const person=chosenMovie?.details?.credits?.cast?.find(p=>p.id===Number(input.dataset.actor));input.disabled=blocked||!person?.profile_path});
 document.querySelectorAll('[data-art]').forEach(input=>input.disabled=blocked||!!artworkItems[Number(input.dataset.art)]?.local);
 const actorSave=$('.actors-area>button');if(actorSave)actorSave.disabled=blocked||!document.querySelector('[data-actor]:checked:not(:disabled)');
 document.querySelectorAll('.primary-video').forEach(box=>box.disabled=blocked);document.querySelectorAll('.actor-select-all,.actor-clear,.art-select-all,.art-clear').forEach(button=>button.disabled=blocked);
 updateSavedButtons();
 if($('#config-backup-create'))$('#config-backup-create').disabled=!editToken;document.querySelectorAll('.download-config-backup').forEach(button=>button.disabled=!editToken);
 if(typeof applyTVEditState==='function')applyTVEditState();
 $('#image-download').disabled=blocked||!!artworkItems[activeArtwork]?.local;
}

const changeDialog=document.createElement('dialog');changeDialog.id='change-review';changeDialog.innerHTML='<div class="dialog-head"><h2>Review changes</h2><button type="button" id="review-close" class="icon" aria-label="Close review">×</button></div><div id="review-content"></div><div class="actions"><button type="button" class="secondary" id="review-cancel">Cancel</button><button type="button" id="review-confirm">Confirm changes</button></div>';document.body.append(changeDialog);
function confirmChanges(plan,label='Confirm changes'){
 const fields=plan.metadata||[],files=plan.files||[],renames=plan.renames||[];
 const selectable=!!plan.selection_supported;
 const initiallyChecked=id=>plan.replacement_default!==false||!id.startsWith('file:')||!String(files.find(f=>'file:'+f.path===id)?.action||'').toLowerCase().includes('replace');
 const choice=(id,label)=>selectable?`<label class="check review-choice"><input type="checkbox" data-review-operation="${esc(id)}" ${initiallyChecked(id)?'checked':''}>${label}</label>`:label;
 $('#review-content').innerHTML=`${selectable?'<label class="check review-select-all"><input id="review-select-all" type="checkbox" checked>Select all changes</label><p class="muted">Select the files you want to write; unticked files are kept. Metadata fields are saved together in their NFO file. Connected video/subtitle renames stay together.</p>':''}${plan.thumbnails?.requested?`<section class="thumbnail-summary"><h3>Episode thumbnails</h3><p>${plan.thumbnails.ready} ready to download · ${plan.thumbnails.skipped_existing} already saved · ${plan.thumbnails.unavailable} unavailable · ${plan.thumbnails.unmatched} unmatched files</p></section>`:''}${plan.folder_before!==plan.folder_after?`<p>${choice('rename:folder','<strong>Rename folder</strong>')}<br>${esc(plan.folder_before)} → ${esc(plan.folder_after)}</p>`:''}${fields.length?'<h3>Proposed NFO metadata</h3><p class="muted">Written only for NFO files selected below.</p><div class="review-table"><table><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>'+fields.map(f=>`<tr><th>${esc(f.field.replaceAll('_',' '))}</th><td>${esc(f.before||'(empty)')}</td><td>${esc(f.after||'(empty)')}</td></tr>`).join('')+'</tbody></table></div>':''}${renames.length?'<h3>File renames</h3>'+choice('rename:files','Rename these connected files')+renames.map(r=>`<p>${esc(r.before)} → ${esc(r.after)}</p>`).join(''):''}<h3>Files to write</h3>${files.map((f,i)=>`<article class="review-file">${choice('file:'+f.path,`<strong data-review-file-index="${i}">${esc(f.action)}: ${esc(f.path)}</strong>`)}${f.before_url||f.after_url?`<div class="review-images">${f.before_url?`<figure><img src="${esc(f.before_url)}" alt="Current image"><figcaption>Before</figcaption></figure>`:''}${f.after_url?`<figure><img src="${esc(f.after_url)}" alt="Proposed image"><figcaption>After</figcaption></figure>`:''}</div>`:''}</article>`).join('')||'<p>No files selected.</p>'}<p class="muted">Replaced metadata and images are backed up. Video files are never deleted.</p>`;
 $('#review-confirm').textContent=label;$('#review-confirm').disabled=!files.length&&!renames.length&&plan.folder_before===plan.folder_after;
 if(selectable){const boxes=[...changeDialog.querySelectorAll('[data-review-operation]')],all=$('#review-select-all');const sync=()=>{const moving=boxes.find(b=>b.dataset.reviewOperation==='rename:files')?.checked!==false;changeDialog.querySelectorAll('[data-review-file-index]').forEach(node=>{const file=files[Number(node.dataset.reviewFileIndex)];node.textContent=(moving?file.action:file.without_rename_action||file.action)+': '+(moving?file.path:file.without_rename||file.path)});const count=boxes.filter(b=>b.checked).length;all.checked=count===boxes.length&&count>0;all.indeterminate=count>0&&count<boxes.length;$('#review-confirm').disabled=!count};all.onchange=()=>{boxes.forEach(b=>b.checked=all.checked);sync()};boxes.forEach(b=>b.onchange=sync);sync()}

 return new Promise(resolve=>{
  const finish=value=>{if(value&&selectable)plan.selected_operations=[...changeDialog.querySelectorAll('[data-review-operation]:checked')].map(b=>b.dataset.reviewOperation);changeDialog.close();resolve(value)};
  $('#review-confirm').onclick=()=>finish(true);$('#review-close').onclick=()=>finish(false);$('#review-cancel').onclick=()=>finish(false);changeDialog.oncancel=e=>{e.preventDefault();finish(false)};changeDialog.showModal();
 });
}
async function reviewedWrite(payload){
 if(!editToken||libraryReadOnly)throw new Error(libraryReadOnly?'Server is in read-only mode.':'Unlock the padlock before saving.');
 const settings=await api('/api/settings');
 const plan=await api('/api/write/preview',{method:'POST',body:JSON.stringify(payload)});
 if(!payload.artwork_only)plan.replacement_default=!!settings.overwrite;
 if(!await confirmChanges(plan,'Save reviewed changes'))return null;
 return api('/api/write',{method:'POST',body:JSON.stringify({...payload,review_token:plan.review_token,selected_operations:plan.selected_operations})});
}

const scanProgress=document.createElement('section');scanProgress.className='scan-progress';scanProgress.hidden=true;scanProgress.innerHTML='<div class="scan-progress-head"><strong id="scan-summary">Scanning</strong><button type="button" class="secondary" id="scan-cancel">Cancel scan</button></div><progress id="scan-meter" value="0" max="1"></progress><p id="scan-current" class="muted"></p><details id="scan-errors" hidden><summary>Scan errors</summary><ul></ul></details>';$('main').prepend(scanProgress);
$('#scan-cancel').onclick=async()=>{try{await api('/api/scan/cancel',{method:'POST'});$('#scan-cancel').disabled=true;$('#scan-current').textContent='Stopping after the current file…'}catch(e){setLibraryStatus(e.message)}};
async function pollScan(){
 clearTimeout(scanPollTimer);
 try{
  const job=await api('/api/scan/status'),active=['running','cancelling'].includes(job.state);if(typeof libraryMode!=='undefined'&&libraryMode!=='movies'){if(active)scanPollTimer=setTimeout(pollScan,1000);else await reloadLibrary();return}scanProgress.hidden=false;$('#scan').disabled=active;$('#scan-cancel').hidden=!active;$('#scan-cancel').disabled=job.state==='cancelling';
  $('#scan-meter').max=job.total||1;$('#scan-meter').value=job.checked||0;
  $('#scan-summary').textContent=`${job.checked||0} / ${job.total||0} folders · ${job.found||0} movies · ${job.new||0} new · ${job.errors||0} errors · ${Math.floor((job.elapsed||0)/60)}m ${Math.floor((job.elapsed||0)%60)}s`;
  $('#scan-current').textContent=job.state==='cancelling'?'Stopping after the current file…':job.current||'';
  $('#scan-errors').hidden=!(job.warnings?.length||job.error);$('#scan-errors ul').innerHTML=(job.warnings||[]).map(w=>`<li>${esc(w.folder)}: ${esc(w.error)}</li>`).join('')+(job.error?`<li>${esc(job.error)}</li>`:'');
  if(active){scanPollTimer=setTimeout(pollScan,1000)}else{await reloadLibrary();setLibraryStatus(job.state==='failed'?'Scan failed: '+job.error:job.state==='cancelled'?'Scan cancelled. Previously indexed movies are retained.':`Scan complete: ${job.new||0} new movies, ${job.errors||0} errors.`);$('#scan-current').textContent=job.state==='completed'?'Finished':job.state;}
 }catch(e){setLibraryStatus('Unable to read scan progress: '+e.message);$('#scan').disabled=false}
}

function tmdbId(movie){const raw=String(movie.metadata?.tmdbid||'').trim();return /^\d+$/.test(raw)&&Number(raw)>0?String(Number(raw)):''}
function matchesHealth(movie,filter){
 const missing=movie.missing_fields||[];
 if(filter.kind==='unmatched')return !tmdbId(movie);
 if(filter.kind==='poster')return missing.includes('poster');
 if(filter.kind==='fanart')return missing.includes('fanart');
 if(filter.kind==='certificate')return missing.includes('certificate');
 if(filter.kind==='incomplete')return movie.metadata_status!=='complete';
 if(filter.kind==='unreadable')return !!movie.metadata?.error||movie.media?.some(v=>v.probe_note);
 if(filter.kind==='ambiguous')return !!movie.ambiguous_videos?.length;
 if(filter.kind==='duplicates')return filter.id?tmdbId(movie)===filter.id:duplicateGroups.has(tmdbId(movie));
 return true;
}
const healthPanel=document.createElement('section');healthPanel.className='health-dashboard';$('#library-search-panel h2').after(healthPanel);
function useHealthFilter(kind,id=null){resetLibraryFilters();healthFilter={kind,id};renderMovies();updateFilterCount();showWorkspaceTab('search')}
function refreshHealthDashboard(){
 const groups=new Map();movies.forEach(m=>{const id=tmdbId(m);if(id){if(!groups.has(id))groups.set(id,[]);groups.get(id).push(m)}});duplicateGroups=new Map([...groups].filter(([,entries])=>entries.length>1));
 const kinds=[['unmatched','Unmatched'],['poster','Missing poster'],['fanart','Missing background'],['certificate','Missing certificate'],['incomplete','Incomplete metadata'],['unreadable','Unreadable / unprobed'],['ambiguous','Video roles to review'],['duplicates','Possible duplicate movies']];
 const expanded=healthPanel.querySelector('details')?.open;
 healthPanel.innerHTML='<h3>Library health</h3><div class="health-cards">'+kinds.map(([kind,label])=>`<button type="button" data-health="${kind}" aria-pressed="${healthFilter?.kind===kind}"><strong>${movies.filter(m=>matchesHealth(m,{kind})).length}</strong><span>${label}</span></button>`).join('')+'</div>'+ (duplicateGroups.size?`<details ${expanded?'open':''}><summary>${duplicateGroups.size} shared TMDB IDs — review editions or duplicates</summary>${[...duplicateGroups].map(([id,entries])=>`<button class="duplicate-group" type="button" data-duplicate-id="${id}">TMDB ${esc(id)} · ${entries.length} folders: ${esc(entries.map(m=>m.name).join(' / '))}</button>`).join('')}</details>`:'');
 healthPanel.querySelectorAll('[data-health]').forEach(button=>button.onclick=()=>useHealthFilter(button.dataset.health));healthPanel.querySelectorAll('[data-duplicate-id]').forEach(button=>button.onclick=()=>useHealthFilter('duplicates',button.dataset.duplicateId));
}
function addDuplicateNotice(){const id=String(chosenMovie.details.id||'');const group=duplicateGroups.get(id);if(!group)return;const note=document.createElement('div');note.className='warning';note.textContent=`${group.length} folders share TMDB ID ${id}. They may be duplicates or different editions. `;const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Review these movies';button.onclick=()=>useHealthFilter('duplicates',id);note.append(button);$('.edit-fields').prepend(note)}

function addBackupControls(){
 const section=document.createElement('details');section.className='backup-panel';section.innerHTML='<summary>Restore previous metadata or artwork</summary><p class="muted">Select a backed-up file to review before restoring it. The current version will also be backed up.</p><div class="backup-list"></div>';$('.rename-area').after(section);
 section.ontoggle=async()=>{if(!section.open)return;const list=section.querySelector('.backup-list');list.textContent='Loading backups…';try{const result=await api('/api/backups?folder='+encodeURIComponent(current.folder));list.innerHTML=result.backups.map(b=>`<div class="backup-row"><span>${esc(b.file)}<small>${esc(new Date(b.created*1000).toLocaleString())}</small></span><button type="button" class="restore-backup" data-backup="${b.id}">Review restore</button></div>`).join('')||'<p class="muted">No previous versions have been backed up for this movie.</p>';list.querySelectorAll('[data-backup]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{const payload={folder:current.folder,backup_id:button.dataset.backup};const plan=await api('/api/backups/preview',{method:'POST',body:JSON.stringify(payload)});if(!await confirmChanges(plan,'Restore reviewed file'))return;await api('/api/backups/restore',{method:'POST',body:JSON.stringify({...payload,review_token:plan.review_token,selected_operations:plan.selected_operations})});const folder=current.folder;await reloadLibrary();const movie=movies.find(m=>m.folder===folder);if(movie)await selectMovie(movie)}catch(e){window.alert(e.message)}finally{applyEditState()}});applyEditState()}catch(e){list.textContent=e.message}};
}

function addMediaRoles(){
 const section=document.createElement('section');section.className='media-roles';
 const ambiguous=new Set(inspectData.ambiguous_videos||[]),videos=inspectData.videos||[],extras=inspectData.extras||[];
 section.innerHTML='<h4>Main movie files</h4><p class="muted">Only selected main files and matching subtitles are eligible for renaming. Extras stay untouched.</p>'+videos.map(v=>`<label class="check"><input class="primary-video" type="checkbox" data-video="${esc(v.name)}" ${videos.length===1&&!ambiguous.has(v.name)?'checked':''}>${esc(v.name)}${ambiguous.has(v.name)?' — review main file / edition':''}</label>`).join('')+(ambiguous.size?'<p class="warning">Multiple versions or a possible extra were found. Select the intended main file(s) explicitly before renaming.</p>':'')+'<h4>Extras excluded from renaming</h4>'+(extras.length?'<ul>'+extras.map(e=>`<li>${esc(e.name)} <small>(${esc(e.category)})</small></li>`).join('')+'</ul>':'<p class="muted">No conventionally named extras detected.</p>');
 $('#video-metadata').before(section);section.querySelectorAll('input').forEach(input=>input.onchange=()=>$('#rename-preview').innerHTML=filenamePreview());$('#rename-preview').innerHTML=filenamePreview();
}
function addCollectionControls(){
 const button=document.createElement('button');button.type='button';button.className='secondary collection-check';button.textContent='Check collection completeness';button.disabled=!chosenMovie.details.id;button.title='Compare this movie’s TMDB collection with the films in your scanned library';$('#edit-set').closest('label').append(button);
 const pane=document.createElement('section');pane.className='collection-completeness wide';pane.hidden=true;$('#edit-set').closest('label').after(pane);
 async function load(refresh=false){
  pane.hidden=false;pane.textContent='Checking TMDB collection…';button.disabled=true;
  try{const result=await api('/api/collections/completeness?movie_id='+chosenMovie.details.id+'&refresh='+refresh);if(!result.collection){pane.textContent=result.message;return}
   pane.innerHTML=`<h4>${esc(result.collection.name)}</h4><p>${result.owned} owned · ${result.missing} missing released films · ${result.upcoming} upcoming · ${result.unknown_date} without a release date</p><ul>${result.parts.map(p=>`<li class="collection-${p.status}"><strong>${esc(p.title)}</strong> ${esc(p.release_date||'Date unknown')} — ${esc({owned:'In your library',missing:'Not in your library',upcoming:'Upcoming',unknown_date:'Not owned; release date unknown'}[p.status])}${p.owned_folders.map(f=>` <button type="button" class="collection-owned secondary" data-folder="${esc(f.folder)}">Open ${esc(f.name)}</button>`).join('')}</li>`).join('')}</ul><p class="muted">Collection data checked ${esc(new Date(result.updated*1000).toLocaleString())}. Membership is compared by TMDB ID, not filename.</p><button class="secondary" type="button" data-refresh-collection>Refresh from TMDB</button>`;
   pane.querySelectorAll('[data-folder]').forEach(b=>b.onclick=()=>{const movie=movies.find(m=>m.folder===b.dataset.folder);if(movie)selectMovie(movie)});pane.querySelector('[data-refresh-collection]').onclick=()=>load(true);
  }catch(e){pane.textContent=e.message}finally{button.disabled=!chosenMovie?.details?.id}
 }
 button.onclick=()=>load();
}

let savedSearchData={builtins:[],searches:[],archived:[]};
const savedSearchPanel=document.createElement('section');savedSearchPanel.className='saved-search-panel';savedSearchPanel.innerHTML='<h3>Saved searches</h3><div class="saved-search-controls"><select id="saved-search-select" aria-label="Saved search"><option value="">Choose a saved search</option></select><button id="load-saved-search" type="button">Load</button><input id="saved-search-name" aria-label="New saved search name" placeholder="Name a new search" maxlength="80"><button id="save-new-search" class="secondary" type="button">Save as new</button><button id="update-saved-search" class="secondary" type="button">Update selected</button><button id="archive-saved-search" class="secondary" type="button">Archive</button></div><p id="saved-search-status" class="muted" role="status"></p><details><summary>Archived searches</summary><div id="archived-search-list"></div></details>';healthPanel.after(savedSearchPanel);
function currentSearchState(){return {filters:{...libraryFilters},missing_fields:[...missingSelection],missing_mode:$('#missing-mode').value,set_exact:setExact,health:healthFilter}}
function selectedSavedSearch(){return [...savedSearchData.builtins,...savedSearchData.searches].find(item=>item.id===$('#saved-search-select').value)}
function updateSavedButtons(){
 const select=$('#saved-search-select');if(!select)return;const custom=savedSearchData.searches.some(item=>item.id===select.value);
 $('#save-new-search').disabled=!editToken;$('#update-saved-search').disabled=!editToken||!custom;$('#archive-saved-search').disabled=!editToken||!custom;$('#load-saved-search').disabled=!selectedSavedSearch();document.querySelectorAll('.restore-search').forEach(b=>b.disabled=!editToken);
}
async function loadSavedSearches(selected=null){
 try{savedSearchData=await api('/api/saved-searches');const select=$('#saved-search-select'),previous=selected||select.value;select.innerHTML='<option value="">Choose a saved search</option><optgroup label="Presets">'+savedSearchData.builtins.map(q=>`<option value="${q.id}">${esc(q.name)}</option>`).join('')+'</optgroup><optgroup label="Your searches">'+savedSearchData.searches.map(q=>`<option value="${q.id}">${esc(q.name)}</option>`).join('')+'</optgroup>';if([...select.options].some(o=>o.value===previous))select.value=previous;
 $('#archived-search-list').innerHTML=savedSearchData.archived.map(q=>`<div class="backup-row"><span>${esc(q.name)}</span><button class="secondary restore-search" type="button" data-id="${q.id}">Restore</button></div>`).join('')||'<p class="muted">No archived searches.</p>';
 document.querySelectorAll('.restore-search').forEach(b=>b.onclick=async()=>{const q=savedSearchData.archived.find(q=>q.id===b.dataset.id);try{await api('/api/saved-searches',{method:'POST',body:JSON.stringify({id:q.id,name:q.name,data:q.data})});await loadSavedSearches(q.id)}catch(e){$('#saved-search-status').textContent=e.message}});updateSavedButtons();
 }catch(e){$('#saved-search-status').textContent=e.message}
}
$('#saved-search-select').onchange=updateSavedButtons;
$('#load-saved-search').onclick=()=>{const query=selectedSavedSearch();if(!query)return;const state=query.data;resetLibraryFilters();Object.assign(libraryFilters,state.filters||{});for(const [key,value]of Object.entries(libraryFilters)){const field=filterPanel.querySelector(`[data-filter="${key}"]`);if(field)field.value=value}for(const key of state.missing_fields||[])missingSelection.add(key);filterPanel.querySelectorAll('[data-missing]').forEach(box=>box.checked=missingSelection.has(box.dataset.missing));$('#missing-mode').value=state.missing_mode||'any';setExact=!!state.set_exact;healthFilter=state.health||null;renderMovies();updateFilterCount();$('#saved-search-status').textContent='Loaded '+query.name};
async function persistSearch(update=false){
 const existing=selectedSavedSearch();const name=update?existing?.name:$('#saved-search-name').value;if(!name?.trim()){$('#saved-search-status').textContent='Enter a name for your saved search.';return;}
 try{const result=await api('/api/saved-searches',{method:'POST',body:JSON.stringify({id:update?existing.id:null,name:name.trim(),data:currentSearchState()})});await loadSavedSearches(result.id);$('#saved-search-status').textContent='Saved '+name.trim()}catch(e){$('#saved-search-status').textContent=e.message}
}
$('#save-new-search').onclick=()=>persistSearch(false);$('#update-saved-search').onclick=()=>persistSearch(true);
$('#archive-saved-search').onclick=async()=>{const selected=selectedSavedSearch();if(!selected)return;try{await api('/api/saved-searches/'+selected.id,{method:'DELETE'});await loadSavedSearches();$('#saved-search-status').textContent='Archived '+selected.name+'; it can be restored below.'}catch(e){$('#saved-search-status').textContent=e.message}};

const diagnosticsPanel=document.createElement('section');diagnosticsPanel.className='diagnostics-panel';diagnosticsPanel.innerHTML='<h3>Deployment checks and diagnostics</h3><p id="system-version" class="muted"></p><div class="diagnostic-actions"><button id="run-deployment-checks" class="secondary" type="button">Run deployment checks</button><button id="test-provider-connections" class="secondary" type="button">Test API connections</button><a class="download-diagnostics" href="/api/diagnostics/report" download>Download diagnostic report</a></div><div id="deployment-results" role="status"></div><div id="provider-results" role="status"></div><p class="muted">Media-path checks do not write test files to your movie share. Diagnostic exports exclude API credentials, edit tokens, request bodies and headers.</p>';$('#settings-panel-about').append(diagnosticsPanel);
async function loadSystemSummary(){try{const data=await api('/api/system');$('#system-version').textContent=`Build ${data.build} · Python ${data.python} · SQLite ${data.sqlite} · ${data.counts.indexed_movies||0} indexed movies · ${data.counts.indexed_shows||0} indexed shows`}catch(e){$('#system-version').textContent=e.message}}
$('#run-deployment-checks').onclick=async()=>{const b=$('#run-deployment-checks');b.disabled=true;$('#deployment-results').textContent='Checking deployment…';try{const result=await api('/api/deployment/checks');$('#deployment-results').innerHTML=`<p><strong>${result.scan_ready?'Ready for scanning':'Scan checks need attention'}</strong> · ${result.write_ready?'Filesystem and mode permit approved writes':'Library edits are restricted or unavailable'}</p><ul class="deployment-checks">${result.checks.map(c=>`<li class="check-${c.status}"><strong>${esc(c.title)} — ${esc(c.status)}</strong><span>${esc(c.detail)}</span></li>`).join('')}</ul>`}catch(e){$('#deployment-results').textContent=e.message}finally{b.disabled=false}};
$('#test-provider-connections').onclick=async()=>{const b=$('#test-provider-connections');b.disabled=true;$('#provider-results').textContent='Checking configured providers…';try{const result=await api('/api/deployment/providers',{method:'POST'});$('#provider-results').innerHTML=result.results.map(c=>`<p class="check-${c.status}"><strong>${esc(c.provider)} — ${esc(c.status)}</strong>: ${esc(c.detail)} ${c.elapsed_ms!==undefined?`(${c.elapsed_ms} ms)`:''}</p>`).join('')}catch(e){$('#provider-results').textContent=e.message}finally{b.disabled=false}};
applyEditState();
restoreWorkspace();

function installActorSelectionControls(){
 const area=$('.actors-area');if(!area||area.querySelector('.actor-selection-controls'))return;
 const controls=document.createElement('div');controls.className='actor-selection-controls';controls.innerHTML='<button type="button" class="secondary actor-select-all">Select all actors</button><button type="button" class="secondary actor-clear">Clear selection</button>';area.querySelector('.actors-list').before(controls);
 controls.querySelector('.actor-select-all').onclick=()=>{area.querySelectorAll('[data-actor]:not(:disabled)').forEach(box=>box.checked=true);applyEditState()};controls.querySelector('.actor-clear').onclick=()=>{area.querySelectorAll('[data-actor]').forEach(box=>box.checked=false);applyEditState()};
}

const maintenancePanel=document.createElement('section');maintenancePanel.className='maintenance-panel';maintenancePanel.innerHTML='<h3>Config backups and NFO checks</h3><p class="muted">Config backups include settings, API keys, SQLite and metadata backup history. Keep them private and copy them away from this server. Movie and TV video shares are not included.</p><button type="button" id="config-backup-create" class="secondary">Create config backup</button><div id="config-backup-list"></div><p id="config-backup-status" role="status"></p><button type="button" id="nfo-sample-check" class="secondary">Check a sample of NFO files</button><div id="nfo-check-results" role="status"></div>';$('#settings-panel-about').append(maintenancePanel);
async function loadConfigBackups(){try{const d=await api('/api/config/backups');$('#config-backup-list').innerHTML=d.backups.map(item=>`<div class="backup-row"><span>${esc(new Date(item.created).toLocaleString())} · ${(item.size_bytes/1024**2).toFixed(1)} MB</span><button type="button" class="download-config-backup" data-backup-id="${item.id}">Download private backup</button></div>`).join('')||'<p class="muted">No config backups yet.</p>';document.querySelectorAll('[data-backup-id]').forEach(button=>button.onclick=async()=>{try{const r=await fetch('/api/config/backups/'+button.dataset.backupId,{headers:{'X-Artflix-Edit-Token':editToken||''}});if(!r.ok)throw new Error('Unlock editing to download this private backup.');const url=URL.createObjectURL(await r.blob()),link=document.createElement('a');link.href=url;link.download='ArtFlix-config-'+button.dataset.backupId.slice(0,8)+'.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000)}catch(e){$('#config-backup-status').textContent=e.message}})}catch(e){$('#config-backup-status').textContent=e.message}}
$('#config-backup-create').onclick=async()=>{const button=$('#config-backup-create');button.disabled=true;$('#config-backup-status').textContent='Creating a consistent SQLite snapshot and config archive…';try{await api('/api/config/backups',{method:'POST'});await loadConfigBackups();$('#config-backup-status').textContent='Backup created. Download a copy to another disk or machine.'}catch(e){$('#config-backup-status').textContent=e.message}finally{button.disabled=!editToken}};
$('#nfo-sample-check').onclick=async()=>{const button=$('#nfo-sample-check');button.disabled=true;$('#nfo-check-results').textContent='Checking up to ten NFO files per library…';try{const d=await api('/api/nfo/sample-check');$('#nfo-check-results').innerHTML='<p>'+esc(d.notice)+'</p>'+d.results.map(row=>`<p><strong>${row.valid?'Pass':'Needs review'}: ${esc(row.library+' / '+row.file)}</strong>${row.issues.map(message=>'<br>'+esc(message)).join('')}</p>`).join('')+(d.results.length?'':'<p>No local NFOs were found.</p>')}catch(e){$('#nfo-check-results').textContent=e.message}finally{button.disabled=false}};

function showSettingsTab(key){
 const buttons=[...document.querySelectorAll('[data-settings-tab]')];
 buttons.forEach(button=>{const active=button.dataset.settingsTab===key;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;document.getElementById(button.getAttribute('aria-controls')).hidden=!active});
 if(key==='about'){loadSystemSummary();if(editToken)loadConfigBackups();else $('#config-backup-status').textContent='Unlock editing to create or download private backups.'}
}
const settingsTabs=[...document.querySelectorAll('[data-settings-tab]')];
settingsTabs.forEach((button,index)=>{button.onclick=()=>showSettingsTab(button.dataset.settingsTab);button.onkeydown=e=>{let next;if(e.key==='ArrowRight')next=(index+1)%settingsTabs.length;if(e.key==='ArrowLeft')next=(index+settingsTabs.length-1)%settingsTabs.length;if(e.key==='Home')next=0;if(e.key==='End')next=settingsTabs.length-1;if(next!==undefined){e.preventDefault();showSettingsTab(settingsTabs[next].dataset.settingsTab);settingsTabs[next].focus()}}});

$('#settings-form').addEventListener('invalid',event=>{const panel=event.target.closest('[role=tabpanel]');if(panel)showSettingsTab(panel.id.replace('settings-panel-',''))},true);

function installArtworkSelectionControls(){
 const area=$('.art-area');if(!area||area.querySelector('.art-selection-controls'))return;
 const controls=document.createElement('div');controls.className='art-selection-controls';controls.innerHTML='<button type="button" class="secondary art-select-all">Select all artwork</button><button type="button" class="secondary art-clear">Clear selection</button><small>Chooses the first available image for each destination; change individual choices as needed.</small>';area.querySelector('.artwork').before(controls);
 controls.querySelector('.art-select-all').onclick=()=>selectArtworkChoices(area,'data-art',artworkItems);
 controls.querySelector('.art-clear').onclick=()=>area.querySelectorAll('[data-art]').forEach(box=>box.checked=false);
}
function selectArtworkChoices(area,attribute,items){
 const outputs=new Set();area.querySelectorAll('['+attribute+']').forEach(box=>{box.checked=false;if(box.disabled)return;const item=items[Number(box.getAttribute(attribute))],names=item.outputs||[item.name];if(names.some(name=>outputs.has(name)))return;names.forEach(name=>outputs.add(name));box.checked=true});
}

const templateSettings=document.createElement('div');templateSettings.className='tv-naming-templates';templateSettings.innerHTML='<label>Saved TV naming template<select id="settings-tv-template"></select></label><button type="button" id="settings-template-use" class="secondary">Use template</button><label>Template name<input id="settings-template-name" maxlength="80" placeholder="Name this format"></label><button type="button" id="settings-template-add" class="secondary">Keep as named template</button><p id="settings-template-status" role="status"></p>';$('#settings-panel-tv').append(templateSettings);
function renderSettingsTemplates(){const select=$('#settings-tv-template');if(select)select.innerHTML=Object.entries(settingsTemplateDraft).map(([name,pattern])=>`<option value="${esc(pattern)}">${esc(name)}</option>`).join('')}
$('#settings-template-use').onclick=()=>{if(!editToken)return;$('#settings-form').elements.tv_filename_format.value=$('#settings-tv-template').value};
$('#settings-template-add').onclick=()=>{if(!editToken)return;const name=$('#settings-template-name').value.trim();if(!name){$('#settings-template-status').textContent='Enter a template name.';return}settingsTemplateDraft[name]=$('#settings-form').elements.tv_filename_format.value;renderSettingsTemplates();$('#settings-template-status').textContent='Template ready. Click Save settings to keep it.'};

function bindMovieArtworkSelection(){document.querySelectorAll('[data-art]').forEach(box=>box.onchange=()=>{if(!box.checked)return;const item=artworkItems[Number(box.dataset.art)];document.querySelectorAll('[data-art]').forEach(other=>{if(other!==box&&artworkItems[Number(other.dataset.art)].name===item.name)other.checked=false})})}
