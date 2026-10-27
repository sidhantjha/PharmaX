import React, { forwardRef, useCallback, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import Globe from 'react-globe.gl';
import type { GlobeMethods } from 'react-globe.gl';
import * as THREE from 'three';
import { feature } from 'topojson-client';
import atlas from 'world-atlas/countries-110m.json';
import northAmerica from './northAmerica.json';
import type { GeometryCollection, Topology } from 'topojson-specification';
import { fitRouteView, focusRouteView, pointAlongPath } from './routeMath';

export type GlobeSignal = {
  id: string; title: string; location: string; impact: string; time: string;
  kind: 'risk' | 'shipment' | 'update'; point: [number, number];
  heading?: string; detail?: string; route?: string; routeId?: string; hospital?: boolean;
};
export type GeoPath = { id: string; kind: string; coords: number[][] };
export type GlobeSceneHandle = { zoomIn: () => void; zoomOut: () => void; reset: () => void; focus: (signal: GlobeSignal) => void; focusPoint: (point: [number,number], altitude?: number) => void };
type Props = {
  signals: GlobeSignal[]; paths: GeoPath[]; routes: boolean; alternate: boolean; alerts: boolean;
  onSelect: (signal: GlobeSignal) => void; theme: 'dark' | 'light'; selectedId?: string; selectedRouteId?: string;
  focusPoint?: [number,number]; active?: boolean;
};

const topology = atlas as unknown as Topology<{countries: GeometryCollection}>;
const coarseCountries = feature(topology,topology.objects.countries).features;
const countries = [...coarseCountries.filter(country=>!['010','840','124','484'].includes(String(country.id))),...northAmerica] as typeof coarseCountries;
const cameraStops = [0.9,1.05,1.25,1.55,2.05,2.75,3.5];

function makeRiskCallout(signal: GlobeSignal,onSelect: Props['onSelect'],selected: boolean) {
  const root=document.createElement('button');
  root.type='button';
  root.className=`risk-map-callout${selected?' selected':''}`;
  root.setAttribute('aria-label',`Open priority risk: ${signal.title}, ${signal.impact}`);
  const pip=document.createElement('span'); pip.className='callout-pip';
  const title=document.createElement('span'); title.className='callout-text'; title.textContent=signal.title;
  const value=document.createElement('span'); value.className='callout-value'; value.textContent=signal.impact;
  root.append(pip,title,value);
  root.addEventListener('click',event=>{event.stopPropagation();onSelect(signal);});
  return root;
}

export const GlobeScene=forwardRef<GlobeSceneHandle,Props>(function GlobeScene(
  {signals,paths,routes,alternate,alerts,onSelect,theme,selectedId,selectedRouteId,focusPoint,active=true},ref
) {
  const globe=useRef<GlobeMethods|undefined>(undefined);
  const stage=useRef<HTMLDivElement>(null);
  const [viewport,setViewport]=useState<[number,number]>([typeof window==='undefined'?1440:window.innerWidth,typeof window==='undefined'?900:window.innerHeight]);
  const markers=useMemo(()=>alerts?signals:[],[signals,alerts]);
  const priorityRisk=markers.find(signal=>signal.kind==='risk'&&!signal.hospital);
  const initialPath=paths.find(path=>path.id==='main'||path.id==='modeled-dependency')??paths.find(path=>path.kind==='risk')??paths[0];
  const initialView=useMemo(()=>{
    if(focusPoint)return {lat:focusPoint[1],lng:focusPoint[0],altitude:.25};
    if(!initialPath)return {lat:0,lng:0,altitude:1.5};
    if(initialPath.id==='modeled-dependency'){const view=fitRouteView(initialPath,{width:viewport[0],height:viewport[1]});return {...view,altitude:Math.max(view.altitude,1.7)};}
    return priorityRisk?focusRouteView(initialPath,{width:viewport[0],height:viewport[1]},priorityRisk.point):fitRouteView(initialPath,{width:viewport[0],height:viewport[1]});
  },[initialPath,priorityRisk,viewport,focusPoint?.[0],focusPoint?.[1]]);
  const pathData=useMemo(()=>routes?paths.filter(path=>path.id!=='alternate'||alternate).flatMap(path=>[{...path,glow:true},{...path,glow:false}]):[],[paths,routes,alternate]);
  const dependencyArcs=useMemo(()=>{
    const path=routes?paths.find(p=>p.id==='modeled-dependency'):undefined;
    return path?path.coords.slice(1).map((end,index)=>({start:path.coords[index],end,exposed:index<2})):[];
  },[paths,routes]);
  const globeMaterial=useMemo(()=>new THREE.MeshPhongMaterial({color:theme==='dark'?'#101619':'#bacbc5',shininess:theme==='dark'?10:1,specular:new THREE.Color(theme==='dark'?'#34454a':'#b7c2b8')}),[theme]);
  React.useEffect(()=>()=>globeMaterial.dispose(),[globeMaterial]);

  const stepZoom=useCallback((direction:number)=>{
    const instance=globe.current;if(!instance)return;
    const current=instance.pointOfView();
    const stops=focusPoint?[.12,.18,.28,.38,.55,.75,1.05,1.55,2.05,2.75,3.5]:cameraStops;
    const nearest=stops.reduce((best,value)=>Math.abs(value-current.altitude)<Math.abs(best-current.altitude)?value:best,stops[0]);
    const next=stops[Math.max(0,Math.min(stops.length-1,stops.indexOf(nearest)+direction))];
    instance.pointOfView({...current,altitude:next},420);
  },[focusPoint?.[0],focusPoint?.[1]]);

  useImperativeHandle(ref,()=>({
    zoomIn:()=>stepZoom(-1),zoomOut:()=>stepZoom(1),reset:()=>globe.current?.pointOfView(initialView,650),
    focusPoint:(point,altitude=0.82)=>globe.current?.pointOfView({lat:point[1],lng:point[0],altitude},900),
    focus:(signal)=>{
      const route=signal.routeId?paths.find(path=>path.id===signal.routeId):undefined;
      const current=globe.current?.pointOfView();
      const view=route?focusRouteView(route,{width:viewport[0],height:viewport[1]},signal.point):{lat:signal.point[1],lng:signal.point[0],altitude:Math.min(current?.altitude??.82,.82)};
      globe.current?.pointOfView(view,650);
    }
  }),[stepZoom,initialView,paths,viewport]);

  const ready=useCallback(()=>{
    const instance=globe.current;if(!instance)return;
    const controls=instance.controls();controls.enableDamping=true;controls.dampingFactor=.085;controls.rotateSpeed=.48;controls.zoomSpeed=.62;controls.enablePan=false;controls.minDistance=115;controls.maxDistance=460;
    const camera=instance.camera() as THREE.PerspectiveCamera;
    camera.near=.5;camera.far=2000;camera.updateProjectionMatrix();
    instance.renderer().setPixelRatio(Math.min(window.devicePixelRatio,1.5));
    instance.renderer().shadowMap.enabled=false;
    instance.pointOfView(initialView,0);
    if(!active)instance.pauseAnimation();
  },[initialView]);

  React.useEffect(()=>{
    // Saved data arrives after the canvas mounts; initialize its modeled network view once.
    if(focusPoint)globe.current?.pointOfView(initialView,650);
    if(initialPath?.id==='modeled-dependency')globe.current?.pointOfView(initialView,650);
  },[initialPath?.id,focusPoint?.[0],focusPoint?.[1]]);

  React.useEffect(()=>{if(active)globe.current?.resumeAnimation();else globe.current?.pauseAnimation();},[active]);
  useLayoutEffect(()=>{
    const element=stage.current;if(!element)return;
    const resize=new ResizeObserver(entries=>{const box=entries[0]?.contentRect;if(box)setViewport([Math.max(1,Math.round(box.width)),Math.max(1,Math.round(box.height))]);});
    resize.observe(element);return()=>resize.disconnect();
  },[]);

  React.useEffect(()=>{
    const instance=globe.current,route=paths.find(path=>path.id==='main');
    if(!instance||!route)return;
    const vessel=new THREE.Group();
    const mat=(color:number,emissive=0)=>new THREE.MeshStandardMaterial({color,emissive,emissiveIntensity:.22,metalness:.16,roughness:.4});
    const hull=new THREE.Mesh(new THREE.BoxGeometry(5.2,1.15,1.3),mat(theme==='dark'?0xf2c58e:0x9a4841,theme==='dark'?0x38200c:0x250a09));hull.position.y=.12;vessel.add(hull);
    const bow=new THREE.Mesh(new THREE.ConeGeometry(.7,1.7,6),mat(theme==='dark'?0xe8d8bd:0x87423c));bow.rotation.z=-Math.PI/2;bow.position.set(3,.14,0);vessel.add(bow);
    const bridge=new THREE.Mesh(new THREE.BoxGeometry(1.05,1.4,.92),mat(theme==='dark'?0xf4f0e7:0x793c38));bridge.position.set(-1.65,1.05,0);vessel.add(bridge);
    const containerMat=mat(theme==='dark'?0x56d4d1:0x168f8a,theme==='dark'?0x0c3334:0x062c29);
    for(let i=0;i<4;i++){const box=new THREE.Mesh(new THREE.BoxGeometry(.76,.65,.69),containerMat);box.position.set(-.8+i*.84,.98,0);vessel.add(box);}
    const isSelected=selectedRouteId==='main';vessel.scale.setScalar(isSelected?0.34:0.3);vessel.userData.illustrative=true;
    instance.scene().add(vessel);
    let frame=0,raf=0,running=true;
    const animate=()=>{
      if(!running)return;
      const progress=.29+.07*(.5-.5*Math.cos(performance.now()/72000*Math.PI*2));
      const current=pointAlongPath(route.coords,progress);
      const pos=instance.getCoords(current.lat,current.lng,.013),next=instance.getCoords(current.nextLat,current.nextLng,.013);
      const surface=new THREE.Vector3(pos.x,pos.y,pos.z),up=surface.clone().normalize();
      const heading=new THREE.Vector3(next.x,next.y,next.z).sub(surface).projectOnPlane(up).normalize();
      const lateral=new THREE.Vector3().crossVectors(heading,up).normalize();
      vessel.position.copy(surface);vessel.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(heading,up,lateral));
      frame++;raf=requestAnimationFrame(animate);
    };
    const start=window.setTimeout(()=>{if(frame===0)animate();},60);
    const canvas=instance.renderer().domElement,raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();
    const hover=(event:PointerEvent)=>{const rect=canvas.getBoundingClientRect();pointer.set(((event.clientX-rect.left)/rect.width)*2-1,-((event.clientY-rect.top)/rect.height)*2+1);raycaster.setFromCamera(pointer,instance.camera());vessel.scale.setScalar(raycaster.intersectObjects(vessel.children,true).length?0.39:isSelected?0.34:0.3);};
    canvas.addEventListener('pointermove',hover);
    return()=>{running=false;clearTimeout(start);cancelAnimationFrame(raf);canvas.removeEventListener('pointermove',hover);instance.scene().remove(vessel);vessel.traverse(object=>{if(object instanceof THREE.Mesh){object.geometry.dispose();const materials=Array.isArray(object.material)?object.material:[object.material];materials.forEach(material=>material.dispose());}});};
  },[paths,theme,selectedRouteId]);

  const htmlElement=useCallback((value:object)=>{
    const signal=value as GlobeSignal;
    if(!signal.hospital)return makeRiskCallout(signal,onSelect,selectedId===signal.id);
    const root=document.createElement('button');root.type='button';
    root.className=`hospital-marker label-${signals.filter(s=>s.hospital).findIndex(s=>s.id===signal.id)} ${signal.kind==='risk'?'hospital-exposed':''} ${selectedId===signal.id?'hospital-selected':''}`;
    root.setAttribute('aria-label',`Inspect simulated hospital: ${signal.title}`);
    const dot=document.createElement('span');dot.className='hospital-cross';dot.textContent='+';
    const label=document.createElement('span');label.className='hospital-map-label';label.textContent=signal.title;
    root.append(dot,label);root.addEventListener('click',event=>{event.stopPropagation();onSelect(signal);});return root;
  },[onSelect,selectedId,signals]);
  return <div className="globe-stage" role="group" aria-label="Interactive three dimensional supply map">
    <div ref={stage} className="globe-viewport"><Globe ref={globe} width={viewport[0]} height={viewport[1]}
      backgroundColor="rgba(0,0,0,0)" backgroundImageUrl={null} globeImageUrl={null} globeMaterial={globeMaterial} globeCurvatureResolution={3}
      showGraticules={theme==='dark'} atmosphereColor={theme==='dark'?'#66818e':'#c2d0c0'} atmosphereAltitude={.025}
      polygonsData={countries} polygonCapColor={()=>theme==='dark'?'#536169':'#e5e4d8'} polygonSideColor={()=>theme==='dark'?'#11171b':'#d5dacd'}
      polygonStrokeColor={()=>theme==='dark'?'#65747d':'#96aca1'} polygonAltitude={theme==='dark'?.002:.002}
      polygonCapCurvatureResolution={2} polygonsTransitionDuration={0} onGlobeReady={ready}
      pathsData={pathData} pathPoints={(d:object)=>((d as GeoPath).coords).map(([lng,lat])=>[lat,lng])}
      pathPointAlt={.009}
      arcsData={dependencyArcs} arcStartLat={(d:object)=>(d as typeof dependencyArcs[number]).start[1]} arcStartLng={(d:object)=>(d as typeof dependencyArcs[number]).start[0]}
      arcEndLat={(d:object)=>(d as typeof dependencyArcs[number]).end[1]} arcEndLng={(d:object)=>(d as typeof dependencyArcs[number]).end[0]}
      arcColor={(d:object)=>(d as typeof dependencyArcs[number]).exposed?(theme==='dark'?'#e48182':'#ad5c51'):(theme==='dark'?'#7bc9d0':'#377e77')}
      arcAltitude={.035} arcStroke={.35} arcDashLength={.45} arcDashGap={.12} arcDashAnimateTime={6500} arcsTransitionDuration={0}
      pathPointLat={(p:number[])=>p[0]} pathPointLng={(p:number[])=>p[1]} pathResolution={1.4} pathTransitionDuration={250}
      pathStroke={(d:object)=>(d as GeoPath&{glow:boolean}).glow?.64:.105}
      pathColor={(d:object)=>{const path=d as GeoPath&{glow:boolean};if(path.kind==='risk')return path.glow?'#a31d2b':'#ff535e';if(path.kind==='shipment')return path.glow?'#174955':'#63cbe0';if(path.kind==='update')return path.glow?'#1f4c51':'#80d6d3';return path.glow?'#333e42':'#829399';}}
      pathDashLength={(d:object)=>(d as GeoPath).kind==='risk'?.19:.58} pathDashGap={(d:object)=>(d as GeoPath).kind==='risk'?.1:.42}
      pathDashAnimateTime={(d:object)=>(d as GeoPath).kind==='risk'?3600:0}
      pointsData={markers} pointLat={(d:object)=>(d as GlobeSignal).point[1]} pointLng={(d:object)=>(d as GlobeSignal).point[0]}
      pointAltitude={.015} pointRadius={(d:object)=>(d as GlobeSignal).hospital?.055:.09} pointResolution={10} pointsMerge={false} pointsTransitionDuration={0}
      pointColor={(d:object)=>{const signal=d as GlobeSignal;if(signal.kind==='risk')return theme==='dark'?'#ff4b57':'#d8433e';if(signal.kind==='shipment')return theme==='dark'?'#63def0':'#148ba8';return theme==='dark'?'#93dbe1':'#279b98';}}
      onPointClick={(point:object)=>onSelect(point as GlobeSignal)} onPointHover={(point:object|null)=>{document.body.style.cursor=point?'pointer':''}}
      ringsData={markers.filter(s=>!s.hospital)} ringLat={(d:object)=>(d as GlobeSignal).point[1]} ringLng={(d:object)=>(d as GlobeSignal).point[0]}
      ringAltitude={.014} ringColor={(d:object)=>{const signal=d as GlobeSignal;if(signal.kind==='risk')return theme==='dark'?'#ff53668a':'#d9464268';return theme==='dark'?'#62cbd052':'#278d9148';}}
      ringMaxRadius={.22} ringPropagationSpeed={(d:object)=>(d as GlobeSignal).kind==='risk'?.5:.19} ringRepeatPeriod={5000}
      htmlElementsData={[...(priorityRisk?[priorityRisk]:[]),...markers.filter(s=>s.hospital)]} htmlLat={(d:object)=>(d as GlobeSignal).point[1]} htmlLng={(d:object)=>(d as GlobeSignal).point[0]}
      htmlAltitude={.034} htmlElement={htmlElement} htmlElementVisibilityModifier={(el,isVisible)=>{el.style.opacity=isVisible?'1':'0';el.style.pointerEvents=isVisible?'auto':'none'}}
      enablePointerInteraction showPointerCursor rendererConfig={{antialias:true,alpha:true,powerPreference:'high-performance'}}
    /></div><div className="globe-edge-falloff" aria-hidden="true"/>
  </div>;
});
