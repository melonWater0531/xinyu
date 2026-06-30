(() => {
  const FACE_NAMES = new Set(['left_eye','right_eye','nose','left_mouth','right_mouth']);
  window.toggleLandmarks = function(){
    landmarkMesh = !landmarkMesh;
    const btn = document.getElementById('landmarkToggle');
    btn.setAttribute('aria-pressed', String(landmarkMesh));
    btn.textContent = landmarkMesh ? 'MediaPipe 468' : 'Five points';
    if (state) window.drawOverlay(state);
  };
  window.drawOverlay = function(s){
    const img=$('videoStream'),canvas=$('overlay'),wrap=$('videoWrap'),ctx=canvas.getContext('2d');
    const w=wrap.clientWidth,h=wrap.clientHeight;
    if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}
    ctx.clearRect(0,0,w,h);
    const video=s.video||{},pose=s.pose||{},rs=getImageDrawRect(img,w,h);
    ctx.lineWidth=2;ctx.font='12px sans-serif';
    (video.detections||[]).forEach(d=>{
      const x=rs.x+d.x*rs.sx,y=rs.y+d.y*rs.sy,bw=d.w*rs.sx,bh=d.h*rs.sy;
      ctx.strokeStyle='#2563eb';ctx.strokeRect(x,y,bw,bh);ctx.fillStyle='#2563eb';
      ctx.fillText(`${d.class_name||'obj'} ${Math.round((d.confidence||0)*100)}%`,x+4,Math.max(14,y-4));
    });
    const locked=s.locked_track_id??(s.control||{}).locked_track_id;
    (pose.persons||[]).filter(p=>Number(p.lost_frames||0)===0).forEach(p=>{
      const selected=locked==null||String(p.track_id)===String(locked),box=p.bbox||[];
      if(box.length===4){
        ctx.strokeStyle=selected?'#138a5b':'rgba(19,138,91,.32)';ctx.lineWidth=selected?2:1;
        ctx.strokeRect(rs.x+box[0]*rs.sx,rs.y+box[1]*rs.sy,(box[2]-box[0])*rs.sx,(box[3]-box[1])*rs.sy);
      }
      if(!selected)return;
      (p.keypoints||[]).filter(k=>FACE_NAMES.has(k.name)).forEach(k=>{
        ctx.fillStyle='#10b981';ctx.beginPath();ctx.arc(rs.x+k.x*rs.sx,rs.y+k.y*rs.sy,3.5,0,Math.PI*2);ctx.fill();
      });
    });
    if(landmarkMesh){
      ctx.fillStyle='rgba(16,185,129,.58)';
      (((s.mp_face||{}).landmarks_mesh)||[]).forEach(p=>{
        ctx.beginPath();ctx.arc(rs.x+p[0]*rs.sx,rs.y+p[1]*rs.sy,1,0,Math.PI*2);ctx.fill();
      });
    }
    const mirror=$('multiOverlay'),mirrorWrap=$('multiVideoWrap');
    if(mirror&&mirrorWrap){
      const mw=mirrorWrap.clientWidth,mh=mirrorWrap.clientHeight;
      if(mirror.width!==mw||mirror.height!==mh){mirror.width=mw;mirror.height=mh}
      const mctx=mirror.getContext('2d');mctx.clearRect(0,0,mw,mh);
      mctx.drawImage(canvas,0,0,w,h,0,0,mw,mh);
    }
  };
  function finishStop(result,label){
    const runtime=(result&&result.runtime)||{};
    const failed=!result||!result.accepted||runtime.stop_state==='hardware_stop_failed'||result.reason==='hardware_error';
    clearSession();
    log(failed?`${label}: hardware stop failed`:`${label}: stopped`);
    return !failed;
  }
  window.stopSinglePage=async function(){
    const result=await apiPost('/api/single_track/stop',{session_id:currentSessionId});
    finishStop(result,'single tracking');
  };
  window.pauseMultiYawPage=async function(){
    const result=await apiPost('/api/multi_track/stop',{finalize:false,session_id:currentSessionId});
    finishStop(result,'speaker tracking');
  };
  window.stopMultiYawPage=async function(){
    await window.pauseMultiYawPage();
  };
  window.stopManualPage=async function(){
    const result=await apiPost('/api/control/manual/stop',{session_id:currentSessionId});
    finishStop(result,'manual gimbal');
  };
})();
