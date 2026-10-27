// @vitest-environment jsdom
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DesignReferenceApp as App } from './App';
import { fitRouteView, focusRouteView, pointAlongPath } from './routeMath';

vi.mock('./GlobeScene', async () => {
  const ReactModule = await import('react');
  return { GlobeScene: ReactModule.forwardRef(function MockGlobe({ signals, onSelect }: { signals: {id:string;title:string;impact:string}[]; onSelect:(signal:unknown)=>void }, ref) {
    ReactModule.useImperativeHandle(ref, () => ({ zoomIn(){}, zoomOut(){}, reset(){}, focus(){} }));
    return <div data-testid="persistent-globe">{signals.map(signal=><button key={signal.id} aria-label={signal.title} onClick={()=>onSelect(signal)}>{signal.title}</button>)}</div>;
  }) };
});

afterEach(()=>{cleanup();localStorage.clear();});

describe('route geometry helpers',()=>{
  it('fits a route from its coordinates and viewport, including antimeridian paths',()=>{
    const path={id:'west',kind:'update',coords:[[170,20],[-170,25]]};
    const wide=fitRouteView(path,{width:1440,height:900});
    const tall=fitRouteView(path,{width:390,height:844});
    expect(Math.abs(wide.lng)).toBeCloseTo(180,0);
    expect(wide.altitude).toBeLessThan(tall.altitude);
    expect(wide.altitude).toBeGreaterThan(0);
  });

  it('interpolates the vessel only along the existing route coordinates',()=>{
    const point=pointAlongPath([[0,0],[10,0],[20,10]],.5);
    expect(point.lng).toBeGreaterThan(9);
    expect(point.lng).toBeLessThan(20);
    expect(point.nextLng).toBe(20);
  });

  it('centers the initial camera on the selected issue marker',()=>{
    const path={id:'main',kind:'risk',coords:[[73,19],[66,15],[57,11],[46,12],[43,13],[39,21],[33,30]]};
    const view=focusRouteView(path,{width:1440,height:900},[43,13]);
    expect(view.lat).toBe(13);
    expect(view.lng).toBe(43);
    expect(view.altitude).toBeLessThanOrEqual(.82);
  });
});

describe('risk intelligence experience',()=>{
  it('keeps the same globe mounted while opening a concise evidence-first risk view',async()=>{
    const user=userEvent.setup();
    const {container}=render(<App/>);
    const globe=await screen.findByTestId('persistent-globe');
    await user.click(screen.getByRole('button',{name:'Red Sea disruption'}));
    expect(screen.getByRole('heading',{level:1,name:'Red Sea disruption'})).toBeTruthy();
    expect(screen.getByRole('link',{name:/IMO · Red Sea area/})).toBeTruthy();
    expect(screen.getByRole('link',{name:/UNCTAD · Maritime Transport 2025/})).toBeTruthy();
    expect(screen.getByText(/seven-day delay is an assumption/i)).toBeTruthy();
    expect(screen.getByText(/8 demo steps/)).toBeTruthy();
    expect(screen.getByText('Trade Specialist')).toBeTruthy();
    expect(screen.getAllByText('Shipping Specialist').length).toBeGreaterThan(1);
    expect(container.querySelectorAll('.agent-log-row')).toHaveLength(8);
    expect(new Set(Array.from(container.querySelectorAll('.agent-name')).map(node=>node.textContent)).size).toBe(6);
    expect(container.querySelector('[data-testid="persistent-globe"]')).toBe(globe);
    await user.click(screen.getByRole('button',{name:'Full map'}));
    expect(screen.queryByRole('heading',{level:1,name:'Red Sea disruption'})).toBeNull();
  });

  it('records review and approval locally only after confirmation',async()=>{
    const user=userEvent.setup();render(<App/>);
    await user.click(screen.getByRole('button',{name:'Red Sea disruption'}));
    await user.click(screen.getByRole('button',{name:'Review'}));
    expect(screen.getByText('Mumbai → Rotterdam via Red Sea')).toBeTruthy();
    expect(screen.getByText(/mock comparison/)).toBeTruthy();
    await user.click(screen.getByRole('button',{name:'Approve locally'}));
    expect(screen.getByRole('alertdialog')).toBeTruthy();
    await user.click(screen.getByRole('button',{name:'Confirm locally'}));
    expect(screen.getByRole('status').textContent).toMatch(/No route or shipment was changed/);
  });

  it('shows readable sidebar labels and persists a complete theme selection',async()=>{
    const user=userEvent.setup();render(<App/>);
    expect(screen.getByRole('navigation',{name:'Main navigation'}).textContent).toContain('Overview');
    const toggle=screen.getByRole('button',{name:'Switch to light mode'});
    await user.click(toggle);
    expect(document.querySelector('.app-shell')?.classList.contains('theme-light')).toBe(true);
    expect(localStorage.getItem('pharmax-theme')).toBe('light');
    expect(screen.getByRole('button',{name:'Switch to dark mode'})).toBeTruthy();
  });

  it('puts the root risk first and removes the requested controls',async()=>{
    render(<App/>);
    const riskRows=document.querySelectorAll('.alerts-overlay .alert-row');
    expect(riskRows[0]?.classList.contains('alert-priority')).toBe(true);
    expect(screen.queryByText('Simulated demo')).toBeNull();
    expect(screen.queryByRole('button',{name:'Workspace'})).toBeNull();
    expect(screen.queryByRole('button',{name:'Layers'})).toBeNull();
    expect(screen.queryByRole('button',{name:'Help'})).toBeNull();
    expect(document.querySelector('.avatar')).toBeNull();
    expect(screen.getByRole('button',{name:'Zoom in'})).toBeTruthy();
  });

  it('keeps chat behind its trigger and provides clearly local demo replies',async()=>{
    const user=userEvent.setup();render(<App/>);
    expect(screen.queryByRole('textbox',{name:'Message PharmaX'})).toBeNull();
    await user.click(screen.getByRole('button',{name:'Ask PharmaX'}));
    const dialog=await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button',{name:'Explain route risk'}));
    expect(within(dialog).getByText(/scenario assumption/i)).toBeTruthy();
    expect(within(dialog).getByText('Scripted demo. No model calls.')).toBeTruthy();
  });
});
