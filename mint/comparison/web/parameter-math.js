'use strict';

// Geometry derived from the standard OpenPose-21 points, in metres. These
// quantities are deliberately separate from any model's native MANO parameters.
const ParameterMath=(()=>{
  const EPSILON=1e-12,DEGREES=180/Math.PI;
  const FINGERS=[
    {id:'thumb',label:'拇指',chain:[0,1,2,3,4],jointNames:['CMC','MCP','IP']},
    {id:'index',label:'食指',chain:[0,5,6,7,8],jointNames:['MCP','PIP','DIP']},
    {id:'middle',label:'中指',chain:[0,9,10,11,12],jointNames:['MCP','PIP','DIP']},
    {id:'ring',label:'无名指',chain:[0,13,14,15,16],jointNames:['MCP','PIP','DIP']},
    {id:'pinky',label:'小指',chain:[0,17,18,19,20],jointNames:['MCP','PIP','DIP']}
  ];
  const validPoint=point=>Array.isArray(point)&&point.length===3&&[0,1,2].every(index=>Number.isFinite(point[index]));
  const validHand=points=>Array.isArray(points)&&points.length===21&&Array.from(points).every(validPoint);
  const validMatrix=(matrix,rows=3,columns=3)=>Number.isInteger(rows)&&rows>0&&Number.isInteger(columns)&&columns>0&&
    Array.isArray(matrix)&&matrix.length===rows&&Array.from(matrix).every(row=>Array.isArray(row)&&row.length===columns&&Array.from(row).every(Number.isFinite));
  const validIntrinsics=K=>validMatrix(K)&&K[0][0]>0&&K[1][1]>0&&Math.abs(K[2][0])<=EPSILON&&Math.abs(K[2][1])<=EPSILON&&Math.abs(K[2][2]-1)<=EPSILON;
  const subtract=(a,b)=>a.map((value,index)=>value-b[index]);
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  function normalize(vector){
    if(!validPoint(vector))return null;
    const length=Math.hypot(...vector);
    return Number.isFinite(length)&&length>EPSILON?vector.map(value=>value/length):null;
  }
  function angleDeg(a,b,c){
    if(!validPoint(a)||!validPoint(b)||!validPoint(c))return null;
    const u=normalize(subtract(a,b)),v=normalize(subtract(c,b));
    if(!u||!v)return null;
    return Math.acos(Math.max(-1,Math.min(1,u.reduce((sum,value,index)=>sum+value*v[index],0))))*DEGREES;
  }
  function palmGeometry(points){
    if(!validHand(points))return null;
    const toIndex=normalize(subtract(points[5],points[0])),toPinky=normalize(subtract(points[17],points[0]));
    if(!toIndex||!toPinky)return null;
    // Dividing before adding avoids overflow for otherwise finite coordinates.
    const middle=points[5].map((value,index)=>value/2+points[17][index]/2);
    const forward=normalize(subtract(middle,points[0])),normal=normalize(cross(toIndex,toPinky));
    if(!forward||!normal)return null;
    const lateral=normalize(cross(forward,normal));
    if(!lateral)return null;
    // Camera axes: X right, Y down, Z forward. Positive elevation follows +Y.
    // This is a landmark-plane normal, not a palm-facing claim or MANO rotation.
    return {forward,lateral,normal,normalAzimuthDeg:Math.atan2(normal[0],normal[2])*DEGREES,
      normalElevationDeg:Math.atan2(normal[1],Math.hypot(normal[0],normal[2]))*DEGREES};
  }
  function fingerGeometry(points){
    if(!validHand(points))return null;
    return FINGERS.map(finger=>{
      const anglesDeg=[1,2,3].map(index=>angleDeg(points[finger.chain[index-1]],points[finger.chain[index]],points[finger.chain[index+1]]));
      // The interior angle and 180-angle are geometric bend proxies. In
      // particular, wrist-MCP-PIP is not an anatomical MCP rotation estimate.
      return {id:finger.id,label:finger.label,jointNames:finger.jointNames.slice(),jointIndices:finger.chain.slice(1,4),
        anglesDeg,flexionProxyDeg:anglesDeg.map(value=>value===null?null:180-value)};
    });
  }
  function handMetrics(points,{valid=true,slot=0,space='camera'}={}){
    if(valid!==true||!validHand(points)||(slot!==0&&slot!==1)||(space!=='camera'&&space!=='world'))return null;
    const wristM=points[0].slice(),wristCm=wristM.map(value=>value*100);
    if(!wristCm.every(Number.isFinite))return null;
    return {slot,side:slot===0?'left':'right',space,wristM,wristCm,cameraDepthM:space==='camera'?wristM[2]:null,
      palm:palmGeometry(points),fingers:fingerGeometry(points)};
  }
  function wristDistanceM(leftPoints,rightPoints){
    if(!validHand(leftPoints)||!validHand(rightPoints))return null;
    const distance=Math.hypot(...subtract(leftPoints[0],rightPoints[0]));
    return Number.isFinite(distance)?distance:null;
  }
  function handPairMetrics(pointsA,pointsB,KA,KB){
    if(!validHand(pointsA)||!validHand(pointsB))return null;
    // These are unaligned differences in the common camera coordinate system,
    // not accuracy claims, MANO differences, or wrist-aligned pose errors.
    const jointDifferencesMm=pointsA.map((point,index)=>Math.hypot(...subtract(point,pointsB[index]))*1000);
    if(!jointDifferencesMm.every(Number.isFinite))return null;
    const meanJointMm=jointDifferencesMm.reduce((sum,value)=>sum+value/21,0),wristMm=jointDifferencesMm[0];
    let reprojectionDifferencePx=null;
    if(validIntrinsics(KA)&&validIntrinsics(KB)){
      const project=(point,K)=>{
        // A positive depth is required for every joint in both hands. A
        // missing projection does not become zero or shrink the denominator.
        if(point[2]<=0)return null;
        const x=point[0]/point[2],y=point[1]/point[2],pixel=[K[0][0]*x+K[0][1]*y+K[0][2],K[1][0]*x+K[1][1]*y+K[1][2]];
        return pixel.every(Number.isFinite)?pixel:null;
      };
      const distances=pointsA.map((point,index)=>{
        const a=project(point,KA),b=project(pointsB[index],KB);
        return a&&b?Math.hypot(a[0]-b[0],a[1]-b[1]):null;
      });
      if(distances.every(Number.isFinite))reprojectionDifferencePx=distances.reduce((sum,value)=>sum+value/21,0);
    }
    return {jointDifferencesMm,meanJointMm,wristMm,reprojectionDifferencePx};
  }
  function wristSpeed(previous,current){
    // Callers must use the preceding sample's full-resolution frame record,
    // never a previous displayed frame or the subsampled trajectory endpoint.
    const validSample=sample=>sample&&(sample.valid===undefined||sample.valid===true)&&(sample.space===undefined||sample.space==='camera')&&
      Number.isInteger(sample.sampleIndex)&&sample.sampleIndex>=0&&Number.isFinite(sample.timestampS)&&validHand(sample.points);
    if(!validSample(previous)||!validSample(current)||current.sampleIndex!==previous.sampleIndex+1)return null;
    const dtS=current.timestampS-previous.timestampS;
    if(!Number.isFinite(dtS)||dtS<=0)return null;
    const velocityMps=subtract(current.points[0],previous.points[0]).map(value=>value/dtS);
    if(!validPoint(velocityMps))return null;
    const speedMps=Math.hypot(...velocityMps);
    return Number.isFinite(speedMps)?{speedMps,velocityMps,dtS}:null;
  }
  function cameraMetrics(K,width,height){
    if(!validIntrinsics(K)||!Number.isFinite(width)||width<=0||!Number.isFinite(height)||height<=0)return null;
    const fx=K[0][0],fy=K[1][1],cx=K[0][2],cy=K[1][2];
    // Image boundaries are 0..width and 0..height. The two edge angles remain
    // correct for an off-centre principal point, including one outside the crop.
    const horizontalFovDeg=(Math.atan2(cx,fx)+Math.atan2(width-cx,fx))*DEGREES;
    const verticalFovDeg=(Math.atan2(cy,fy)+Math.atan2(height-cy,fy))*DEGREES;
    return {K:K.map(row=>row.slice()),fx,fy,cx,cy,horizontalFovDeg,verticalFovDeg};
  }
  function formatXYZ(pointM,digits=1){
    if(!validPoint(pointM)||!Number.isInteger(digits)||digits<0||digits>20)return '—';
    const centimetres=pointM.map(value=>value*100);
    if(!centimetres.every(Number.isFinite))return '—';
    return centimetres.map((value,index)=>`${['X','Y','Z'][index]} ${value.toFixed(digits)}`).join(' · ')+' cm';
  }
  return Object.freeze({validPoint,validHand,validMatrix,angleDeg,palmGeometry,fingerGeometry,handMetrics,wristDistanceM,handPairMetrics,wristSpeed,cameraMetrics,formatXYZ});
})();

if(typeof globalThis!=='undefined')globalThis.ParameterMath=ParameterMath;
if(typeof module!=='undefined'&&module.exports)module.exports=ParameterMath;
