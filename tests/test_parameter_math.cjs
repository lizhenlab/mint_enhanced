'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const math=require('../mint/comparison/web/parameter-math.js');

const close=(actual,expected,tolerance=1e-9)=>assert.ok(Math.abs(actual-expected)<=tolerance,`${actual} != ${expected}`);
function hand(wrist=[0,0,1]){
  const points=Array.from({length:21},()=>wrist.slice());
  for(const [base,x]of [[1,-.04],[5,-.02],[9,0],[13,.02],[17,.04]]){
    for(let offset=0;offset<4;offset++)points[base+offset]=[wrist[0]+x,wrist[1]-.04-.02*offset,wrist[2]];
  }
  return points;
}
const sample=(points,index,timestampS,extra={})=>({points,sampleIndex:index,timestampS,valid:true,...extra});

test('finite validation preserves valid zero and rejects null, coercion and malformed geometry',()=>{
  assert.equal(math.validPoint([0,0,0]),true);
  for(const point of [null,[],[1,2],[1,2,3,4],[null,0,0],['1',0,0],[NaN,0,0],[Infinity,0,0],[false,0,0]])assert.equal(math.validPoint(point),false);
  assert.equal(math.validPoint(Array(3)),false);assert.equal(math.validHand(Array(21)),false);
  assert.equal(math.validHand(hand()),true);
  assert.equal(math.validHand(hand().slice(0,20)),false);
  assert.equal(math.validHand([...hand(),[0,0,0]]),false);
  const missing=hand();missing[12]=[0,null,0];assert.equal(math.validHand(missing),false);
  assert.equal(math.handMetrics(missing),null);
});

test('wrist values use metres internally, labelled XYZ centimetres and explicit left/right slots',()=>{
  const points=hand([.123,-.045,.6]),left=math.handMetrics(points,{slot:0}),right=math.handMetrics(points,{slot:1});
  assert.equal(left.side,'left');assert.equal(right.side,'right');
  assert.deepEqual(left.wristM,[.123,-.045,.6]);assert.deepEqual(left.wristCm,[12.3,-4.5,60]);assert.equal(left.cameraDepthM,.6);
  assert.equal(math.formatXYZ(left.wristM),'X 12.3 · Y -4.5 · Z 60.0 cm');
  assert.equal(math.formatXYZ([0,0,0]),'X 0.0 · Y 0.0 · Z 0.0 cm');
  assert.equal(math.formatXYZ([0,null,1]),'—');assert.equal(math.formatXYZ([0,0,1],-1),'—');
  assert.equal(math.handMetrics(points,{valid:false}),null);assert.equal(math.handMetrics(points,{valid:null}),null);
  assert.equal(math.handMetrics(points,{slot:2}),null);
  assert.equal(math.handMetrics(points,{space:'world'}).cameraDepthM,null);
  left.wristM[0]=9;assert.equal(points[0][0],.123,'results cannot mutate the input geometry');
});

test('landmark palm basis is orthonormal and its raw normal reverses under a mirror',()=>{
  const points=hand();points[17]=[.02,-.04,1];const palm=math.palmGeometry(points);
  assert.ok(palm);close(palm.forward[0],0);close(palm.forward[1],-1);close(palm.normal[2],1);
  close(Math.hypot(...palm.lateral),1);close(Math.hypot(...palm.forward),1);close(Math.hypot(...palm.normal),1);
  close(palm.forward.reduce((sum,value,index)=>sum+value*palm.normal[index],0),0);
  close(palm.normalAzimuthDeg,0);close(palm.normalElevationDeg,0);
  const mirrored=points.map(point=>[-point[0],point[1],point[2]]),other=math.palmGeometry(mirrored);
  close(other.normal[2],-1);close(Math.abs(other.normalAzimuthDeg),180);
  // Hand slots identify left/right; they do not silently flip a raw plane normal.
  assert.deepEqual(math.handMetrics(points,{slot:0}).palm,math.handMetrics(points,{slot:1}).palm);
});

test('degenerate palm geometry has no invented direction or rotation',()=>{
  const zero=Array.from({length:21},()=>[0,0,0]);assert.equal(math.palmGeometry(zero),null);
  const collinear=hand();collinear[0]=[0,0,0];collinear[5]=[1,0,0];collinear[17]=[2,0,0];assert.equal(math.palmGeometry(collinear),null);
  collinear[17]=[-1,0,0];assert.equal(math.palmGeometry(collinear),null);
  assert.deepEqual(math.handMetrics(zero).wristM,[0,0,0],'a valid zero wrist is still reportable');
});

test('three-point interior angles and 180-angle bend proxies have distinct values',()=>{
  close(math.angleDeg([0,0,0],[1,0,0],[2,0,0]),180);
  close(math.angleDeg([0,0,0],[1,0,0],[1,1,0]),90);
  close(math.angleDeg([0,0,0],[1,0,0],[0,0,0]),0);
  assert.equal(math.angleDeg([0,0,0],[0,0,0],[1,0,0]),null);
  assert.equal(math.angleDeg([null,0,0],[1,0,0],[2,0,0]),null);
  const points=hand(),index=math.fingerGeometry(points).find(finger=>finger.id==='index');
  assert.deepEqual(index.jointNames,['MCP','PIP','DIP']);assert.deepEqual(index.jointIndices,[5,6,7]);
  close(index.anglesDeg[1],180);close(index.flexionProxyDeg[1],0);
  points[7]=[points[6][0]+.02,points[6][1],points[6][2]];
  const bent=math.fingerGeometry(points).find(finger=>finger.id==='index');close(bent.anglesDeg[1],90);close(bent.flexionProxyDeg[1],90);
});

test('OpenPose-21 finger chains include thumb CMC/MCP/IP and handle degenerate joints individually',()=>{
  const points=hand(),fingers=math.fingerGeometry(points);
  assert.deepEqual(fingers.map(finger=>finger.id),['thumb','index','middle','ring','pinky']);
  assert.deepEqual(fingers[0].jointIndices,[1,2,3]);assert.deepEqual(fingers[0].jointNames,['CMC','MCP','IP']);
  assert.deepEqual(fingers[4].jointIndices,[17,18,19]);
  points[6]=points[5].slice();const partial=math.fingerGeometry(points);
  assert.equal(partial[1].anglesDeg[0],null);assert.equal(partial[1].anglesDeg[1],null);assert.equal(partial[1].flexionProxyDeg[1],null);
  assert.ok(Number.isFinite(partial[1].anglesDeg[2]));assert.ok(Number.isFinite(partial[2].anglesDeg[1]));
  points[20]=null;assert.equal(math.fingerGeometry(points),null);
});

test('two-hand wrist distance is symmetric, in metres and never substitutes a missing point',()=>{
  const left=hand([0,0,0]),right=hand([.03,.04,0]);close(math.wristDistanceM(left,right),.05);
  close(math.wristDistanceM(right,left),.05);assert.equal(math.wristDistanceM(left,left),0);
  assert.equal(math.wristDistanceM(left,null),null);
  right[0]=[null,.04,0];assert.equal(math.wristDistanceM(left,right),null);
});

test('equal complete hands have zero raw differences for all 21 joints and their projections',()=>{
  const points=hand(),K=[[1000,0,320],[0,1000,240],[0,0,1]],metrics=math.handPairMetrics(points,points,K,K);
  assert.deepEqual(metrics.jointDifferencesMm,Array(21).fill(0));assert.equal(metrics.meanJointMm,0);assert.equal(metrics.wristMm,0);assert.equal(metrics.reprojectionDifferencePx,0);
  assert.equal(math.handPairMetrics(points.slice(0,20),points,K,K),null);
  assert.equal(math.handPairMetrics(points,[...points,[0,0,1]],K,K),null);
  const missing=points.map(point=>point.slice());missing[20]=[0,null,1];assert.equal(math.handPairMetrics(points,missing,K,K),null);
});

test('hand-pair differences preserve raw translation, millimetre units and the 21-joint mean',()=>{
  const points=hand(),delta=[.003,.004,0],moved=points.map(point=>point.map((value,axis)=>value+delta[axis])),K=[[1000,0,320],[0,1000,240],[0,0,1]];
  const metrics=math.handPairMetrics(points,moved,K,K);metrics.jointDifferencesMm.forEach(value=>close(value,5));close(metrics.meanJointMm,5);close(metrics.wristMm,5);close(metrics.reprojectionDifferencePx,5);
  const oneChanged=points.map(point=>point.slice());oneChanged[20][0]+=.021;
  const one=math.handPairMetrics(points,oneChanged,K,K);close(one.jointDifferencesMm[20],21);close(one.meanJointMm,1);assert.equal(one.wristMm,0);close(one.reprojectionDifferencePx,1);
});

test('behind-camera joints and unavailable intrinsics retain 3D differences but have no projection metric',()=>{
  const points=hand(),K=[[500,0,320],[0,500,240],[0,0,1]];
  for(const depth of [0,-1]){
    const changed=points.map(point=>point.slice());changed[20][2]=depth;
    const metrics=math.handPairMetrics(points,changed,K,K);assert.ok(metrics.meanJointMm>0);assert.equal(metrics.wristMm,0);assert.equal(metrics.reprojectionDifferencePx,null);
  }
  assert.equal(math.handPairMetrics(points,points,null,K).reprojectionDifferencePx,null);
  assert.equal(math.handPairMetrics(points,points,K,[[500,0,null],[0,500,240],[0,0,1]]).reprojectionDifferencePx,null);
  assert.equal(math.handPairMetrics(points,null,K,K),null);
  const shallow=hand([0,0,.0001]);assert.equal(math.handPairMetrics(shallow,shallow,K,K).reprojectionDifferencePx,0,'projection depth gate is strictly z > 0');
});

test('each hand uses its own intrinsics for projected difference and nonfinite results remain unavailable',()=>{
  const points=hand(),KA=[[500,0,320],[0,500,240],[0,0,1]],KB=[[500,0,326],[0,500,248],[0,0,1]],metrics=math.handPairMetrics(points,points,KA,KB);
  assert.equal(metrics.meanJointMm,0);assert.equal(metrics.wristMm,0);close(metrics.reprojectionDifferencePx,10);
  assert.equal(math.handPairMetrics(points,points,KA,[[0,0,320],[0,500,240],[0,0,1]]).reprojectionDifferencePx,null);
  assert.equal(math.handPairMetrics(hand([1e308,0,1]),hand([-1e308,0,1]),KA,KB),null);
});

test('wrist camera velocity uses exact adjacent sample timestamps, including irregular time steps',()=>{
  const a=sample(hand([0,0,1]),3,10),b=sample(hand([.03,.04,1]),4,10.1),c=sample(hand([.09,.12,1]),5,10.3);
  const first=math.wristSpeed(a,b),second=math.wristSpeed(b,c);
  close(first.dtS,.1);close(first.speedMps,.5);close(first.velocityMps[0],.3);close(first.velocityMps[1],.4);close(first.velocityMps[2],0);
  close(second.dtS,.2);close(second.speedMps,.5);
  assert.deepEqual(math.wristSpeed(sample(hand(),0,0),sample(hand(),1,.5)),{speedMps:0,velocityMps:[0,0,0],dtS:.5});
});

test('speed rejects skipped samples, missing geometry, invalid flags and nonpositive or missing time',()=>{
  const a=sample(hand(),0,0),b=sample(hand(),1,.1);
  assert.equal(math.wristSpeed(a,{...b,sampleIndex:2}),null,'subsampled trajectories cannot estimate frame velocity');
  assert.equal(math.wristSpeed({...a,sampleIndex:null},b),null);
  for(const timestampS of [null,undefined,NaN,Infinity,0,-.1])assert.equal(math.wristSpeed(a,{...b,timestampS}),null);
  for(const valid of [false,null,0,'true'])assert.equal(math.wristSpeed(a,{...b,valid}),null);
  assert.equal(math.wristSpeed(a,{...b,space:'world'}),null);
  assert.equal(math.wristSpeed({...a,space:'world'},b),null);
  const missing=hand();missing[10]=[0,null,1];assert.equal(math.wristSpeed(a,{...b,points:missing}),null);
  assert.equal(math.wristSpeed(null,b),null);
});

test('camera intrinsics and central field of view use the original image dimensions',()=>{
  const K=[[500,0,320],[0,500,240],[0,0,1]],camera=math.cameraMetrics(K,640,480);
  assert.deepEqual([camera.fx,camera.fy,camera.cx,camera.cy],[500,500,320,240]);
  close(camera.horizontalFovDeg,2*Math.atan(640/1000)*180/Math.PI);
  close(camera.verticalFovDeg,2*Math.atan(480/1000)*180/Math.PI);
  camera.K[0][0]=1;assert.equal(K[0][0],500,'returned matrix must not mutate the source K');
});

test('field of view sums separate edge angles for an offset principal point',()=>{
  const K=[[500,0,100],[0,400,50],[0,0,1]],camera=math.cameraMetrics(K,640,480);
  close(camera.horizontalFovDeg,(Math.atan(100/500)+Math.atan(540/500))*180/Math.PI);
  close(camera.verticalFovDeg,(Math.atan(50/400)+Math.atan(430/400))*180/Math.PI);
  assert.ok(Math.abs(camera.horizontalFovDeg-2*Math.atan(320/500)*180/Math.PI)>5);
  const outside=math.cameraMetrics([[500,0,-100],[0,400,550],[0,0,1]],640,480);
  close(outside.horizontalFovDeg,(Math.atan(740/500)-Math.atan(100/500))*180/Math.PI);
  assert.ok(outside.verticalFovDeg>0);
});

test('camera matrices reject non-finite and malformed values without converting null to zero',()=>{
  const K=[[500,0,320],[0,500,240],[0,0,1]];
  assert.equal(math.validMatrix(K),true);
  assert.equal(math.validMatrix(Array(3)),false);assert.equal(math.validMatrix([Array(3),Array(3),Array(3)]),false);
  for(const invalid of [null,[[1,0],[0,1]],[[500,0,null],[0,500,240],[0,0,1]],[[500,0,320],[0,NaN,240],[0,0,1]],[[500,0,320],[0,500,240],[0,0,'1']]]){
    assert.equal(math.validMatrix(invalid),false);assert.equal(math.cameraMetrics(invalid,640,480),null);
  }
  for(const invalid of [0,-1,null,NaN,Infinity,'640'])assert.equal(math.cameraMetrics(K,invalid,480),null);
  assert.equal(math.cameraMetrics([[0,0,320],[0,500,240],[0,0,1]],640,480),null);
  assert.equal(math.cameraMetrics([[500,0,320],[0,-500,240],[0,0,1]],640,480),null);
  assert.equal(math.cameraMetrics([[500,0,320],[0,500,240],[0,1,1]],640,480),null);
});

test('overflow is unavailable rather than an infinite displayed metric',()=>{
  assert.equal(math.handMetrics(hand([1e308,0,1])),null);
  assert.equal(math.formatXYZ([1e308,0,1]),'—');
  assert.equal(math.wristDistanceM(hand([1e308,0,1]),hand([-1e308,0,1])),null);
  assert.equal(math.wristSpeed(sample(hand([-1e308,0,1]),0,0),sample(hand([1e308,0,1]),1,.1)),null);
});

test('the browser namespace works without DOM or Node globals',()=>{
  const sandbox={};vm.runInNewContext(fs.readFileSync(require.resolve('../mint/comparison/web/parameter-math.js'),'utf8'),sandbox);
  assert.equal(typeof sandbox.ParameterMath.wristSpeed,'function');
  assert.equal(vm.runInNewContext('ParameterMath.formatXYZ([0,0,1])',sandbox),'X 0.0 · Y 0.0 · Z 100.0 cm');
  assert.equal(Object.isFrozen(math),true);
});
