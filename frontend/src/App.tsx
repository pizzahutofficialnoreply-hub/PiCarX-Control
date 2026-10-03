import { useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle, ArrowDown, ArrowRight, ArrowUp, Camera, CarFront, CircleStop, Gauge, Home, Joystick, Moon, RotateCcw, Settings, SlidersHorizontal, Sun, Wifi, WifiOff, Waypoints, X } from 'lucide-react';

type Diagnostics = {
  server_connected: boolean; mode: string; hardware_connected: boolean | null;
  library_installed: boolean | null; library_path: string | null; class_path: string | null;
  library_version: string | null; api_methods: Record<string, boolean> | null;
  api_compatible: boolean | null; driver_initialized: boolean | null;
  motor_control_available: boolean | null; steering_servo_available: boolean | null;
  camera_servo_api_available: boolean | null; camera_device_available: boolean | null;
  camera_device_note: string; python_version: string; last_error: string | null;
};
type Status = { mode: string; connected: boolean; connection_error: string | null; motion: string; speed: number; steering: number; calibration: number; pan_calibration: number; tilt_calibration: number; watchdog_ms: number; fault: string | null; last_command_age_ms?: number | null };
type DriveDirection = 'forward' | 'backward' | 'left' | 'right';
const defaultHost = `http://${window.location.hostname || '127.0.0.1'}:8000`;
const tabs = [
  { id: 'home', label: 'Home', icon: Home }, { id: 'camera', label: 'Camera', icon: Camera },
  { id: 'sensors', label: 'Sensors', icon: Activity }, { id: 'logs', label: 'Drive log', icon: Waypoints },
  { id: 'settings', label: 'Settings', icon: Settings },
];
const known = (value: boolean | null | undefined, yes: string, no: string) => value === true ? yes : value === false ? no : 'Unknown';

export default function App() {
  const [tab, setTab] = useState('home');
  const [driveMode, setDriveMode] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [host, setHost] = useState(localStorage.getItem('picarx-host') || defaultHost);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [forwardSpeed, setForwardSpeed] = useState(35);
  const [reverseSpeed, setReverseSpeed] = useState(25);
  const [steering, setSteering] = useState(25);
  const [dark, setDark] = useState(() => localStorage.getItem('picarx-theme') !== 'light');
  const [stick, setStick] = useState({ x: 0, y: 0 });
  const poll = useRef<number>();
  const holdTimer = useRef<number>();
  const activeDirection = useRef<DriveDirection | null>(null);
  const speedRef = useRef({ forward: forwardSpeed, reverse: reverseSpeed, steering });
  speedRef.current = { forward: forwardSpeed, reverse: reverseSpeed, steering };

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${host.replace(/\/$/, '')}${path}`, {
      ...options, headers: { 'Content-Type': 'application/json', ...options?.headers },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Server error (${response.status})`);
    }
    return response.json();
  }
  async function refresh() {
    try {
      const nextStatus = await request('/api/status');
      setStatus(nextStatus);
      setDiagnostics(await request('/api/diagnostics').catch(() => null));
      setConnected(true);
    } catch {
      setConnected(false); setStatus(null); setDiagnostics(null);
    }
  }
  async function connect() {
    localStorage.setItem('picarx-host', host);
    try {
      const health = await request('/api/health');
      setConnected(true);
      setError(health.connected ? '' : (health.connection_error || 'Backend is reachable, but PiCar-X hardware is unavailable.'));
      await refresh();
    } catch {
      setConnected(false); setError('Cannot reach the server. Check its address and Wi-Fi connection.');
    }
  }
  async function stop() {
    if (holdTimer.current) window.clearInterval(holdTimer.current);
    holdTimer.current = undefined; activeDirection.current = null;
    try { const nextStatus = await request('/api/stop', { method: 'POST' }); setStatus(nextStatus as Status); }
    catch { setError('Stop could not reach the server. Turn off vehicle power and check the connection.'); }
  }
  async function drive(direction: DriveDirection) {
    if (!connected || status?.connected === false) return;
    try {
      const nextStatus = await request('/api/drive', { method: 'POST', body: JSON.stringify({ direction, speed: direction === 'backward' ? speedRef.current.reverse : speedRef.current.forward, steering: speedRef.current.steering }) });
      setStatus(nextStatus as Status);
    } catch (e) { setError(e instanceof Error ? e.message : 'Drive command failed'); }
  }
  function beginDrive(direction: DriveDirection) {
    if (!connected || status?.connected === false || activeDirection.current === direction) return;
    if (holdTimer.current) window.clearInterval(holdTimer.current);
    activeDirection.current = direction;
    void drive(direction);
    holdTimer.current = window.setInterval(() => void drive(direction), 180);
  }
  function navigate(nextTab: string) {
    if (driveMode) { void stop(); setDriveMode(false); }
    setTab(nextTab);
  }
  function leaveDrive() { void stop(); setDriveMode(false); }
  useEffect(() => {
    void connect();
    poll.current = window.setInterval(() => void refresh(), 1000);
    const release = () => { if (activeDirection.current) void stop(); };
    const pageGone = () => { void stop(); };
    window.addEventListener('pagehide', pageGone);
    window.addEventListener('blur', release);
    document.addEventListener('visibilitychange', release);
    return () => {
      if (poll.current) window.clearInterval(poll.current);
      if (holdTimer.current) window.clearInterval(holdTimer.current);
      window.removeEventListener('pagehide', pageGone); window.removeEventListener('blur', release);
      document.removeEventListener('visibilitychange', release);
    };
  }, []);
  useEffect(() => { localStorage.setItem('picarx-theme', dark ? 'dark' : 'light'); }, [dark]);
  useEffect(() => {
    if (!connected || status?.connected === false) return;
    const socket = new WebSocket(`${host.replace(/^http/, 'ws').replace(/\/$/, '')}/api/ws`);
    socket.onmessage = event => { try { setStatus(JSON.parse(event.data) as Status); } catch { /* ??? ?? ??? ?????. */ } };
    return () => socket.close();
  }, [connected, host]);

  const motion = status?.motion || 'unknown';
  const motionLabel = !connected ? 'Connection lost' : status?.fault ? 'Error' : status?.connected === false ? 'Hardware unavailable' : motion === 'stopped' ? 'Stopped' : motion === 'forward' ? 'Moving forward' : motion === 'backward' ? 'Reversing' : `${motion} steering`;
  const runMode = diagnostics?.mode ?? status?.mode;
  const app = <div className={dark ? 'app dark' : 'app'}>
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><CarFront size={19}/></span><div><b>PiCar X</b><small>CONTROL STATION</small></div></div>
      <nav>{tabs.map(item => <button className={tab === item.id ? 'nav-item active' : 'nav-item'} key={item.id} onClick={() => navigate(item.id)}><item.icon size={18}/>{item.label}</button>)}</nav>
      <div className="sidebar-bottom"><div className="server-mini"><span className={connected ? 'dot online' : 'dot'}></span>{connected ? 'Server connected' : 'Server offline'}</div><button className="nav-item" onClick={() => setDark(!dark)}>{dark ? <Sun size={18}/> : <Moon size={18}/>}Appearance</button></div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="mobile-brand"><span className="brand-mark"><CarFront size={16}/></span> PiCar X</div><div className="breadcrumb">CONTROL <span>/</span> {tabs.find(item => item.id === tab)?.label.toUpperCase()}</div><div className="top-status"><span className={connected ? 'dot online' : 'dot'}></span>{connected ? 'Connected' : 'Offline'}<span className={`sim-badge ${runMode === 'mock' ? 'mock' : ''}`}>{runMode === 'mock' ? 'MOCK MODE' : runMode === 'hardware' ? 'HARDWARE' : 'MODE UNKNOWN'}</span></div></header>
      {error && <div className="error-banner"><AlertTriangle size={17}/><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16}/></button></div>}
      {tab === 'home' && <>
        <section className="page-heading home-heading"><div><div className="eyebrow">VEHICLE OVERVIEW</div><h1>Good {new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 18 ? 'afternoon' : 'evening'}</h1><p>PiCar-X status and control, all in one place.</p></div><button className="connect-button" onClick={() => void connect()}>{connected ? <><Wifi size={16}/> Refresh</> : <><WifiOff size={16}/> Reconnect</>}</button></section>
        <section className="overview-grid">
          <article className="card hero-card"><div className="hero-copy"><span className="eyebrow">{runMode === 'mock' ? 'SIMULATION' : runMode === 'hardware' ? 'ROBOT STATUS' : 'CONNECTION'}</span><h2>{connected ? motionLabel : 'Vehicle offline'}</h2><p>{runMode === 'mock' ? 'Mock mode is active. Commands do not move a physical vehicle.' : status?.fault || status?.connection_error || 'Status reflects the latest backend report.'}</p><div className="hero-actions"><button className="primary" onClick={() => setDriveMode(true)} disabled={!connected || status?.connected === false}><Joystick size={17}/> Enter drive mode</button><button className="secondary" onClick={() => navigate('settings')}>Settings & diagnostics</button></div></div><div className="hero-icon"><CarFront size={78} strokeWidth={1.2}/><span className={connected ? 'status-orbit connected' : 'status-orbit'} /></div></article>
          <article className="card overview-status"><div className="card-head"><div><h2>Live status</h2><p>Reported by Raspberry Pi</p></div><span className={connected ? 'live' : 'unknown-pill'}><i/>{connected ? 'LIVE' : 'OFFLINE'}</span></div><div className="status-rows"><div><span>Server connection</span><b>{connected ? 'Connected' : 'Unavailable'}</b></div><div><span>Run mode</span><b>{runMode ?? 'Unknown'}</b></div><div><span>Motion</span><b>{connected ? motionLabel : 'Unknown'}</b></div><div><span>PWM command</span><b>{connected && status ? `${status.speed}%` : 'Unknown'}</b></div></div></article>
          <article className="card overview-card"><div className="overview-card-icon"><Activity size={19}/></div><div><span className="eyebrow">SENSORS</span><h3>{diagnostics?.camera_device_available === true ? 'Camera device detected' : 'Sensor values unavailable'}</h3><p>Battery and measured speed are not currently reported by the backend.</p></div><button className="text-button" onClick={() => navigate('sensors')}>View details <ArrowRight size={15}/></button></article>
          <article className="card overview-card"><div className="overview-card-icon"><Camera size={19}/></div><div><span className="eyebrow">CAMERA</span><h3>{diagnostics?.camera_device_available === true ? 'Device detected; stream untested' : 'No camera stream'}</h3><p>{diagnostics?.camera_device_note || 'A live image source is not connected.'}</p></div><button className="text-button" onClick={() => navigate('camera')}>Open camera <ArrowRight size={15}/></button></article>
          <article className="card overview-card overview-wide"><div className="overview-card-icon"><Waypoints size={19}/></div><div><span className="eyebrow">DRIVE HISTORY</span><h3>No recorded drives</h3><p>Drive records are not stored in this version.</p></div><button className="text-button" onClick={() => navigate('logs')}>View drive log <ArrowRight size={15}/></button></article>
        </section>
      </>}
      {tab === 'camera' && <><section className="page-heading"><div><div className="eyebrow">CAMERA</div><h1>Camera view</h1><p>Live PiCar-X video appears here when a stream source is configured.</p></div></section><section className="camera-page card"><div className="camera-placeholder"><div className="camera-empty-icon"><Camera size={30}/></div><span className="eyebrow">VIDEO UNAVAILABLE</span><h2>No camera stream connected</h2><p>{diagnostics?.camera_device_note || 'The backend does not provide a camera video stream yet. No placeholder video is shown.'}</p><span className="badge-muted">Device status: {known(diagnostics?.camera_device_available, 'Detected; image untested', 'Not detected')}</span></div><div className="camera-tools"><b>Pan / Tilt controls</b><p>Servo calibration is available in Settings. Live camera movement controls are not exposed by the current API.</p><button className="secondary" onClick={() => navigate('settings')}>Open camera calibration</button></div></section></>}
      {tab === 'sensors' && <><section className="page-heading"><div><div className="eyebrow">TELEMETRY</div><h1>Sensors & diagnostics</h1><p>Only values explicitly reported by the backend are shown.</p></div><button className="secondary" onClick={() => void refresh()}>Refresh status</button></section><section className="sensor-grid">
        {[['Ultrasonic distance','No sensor endpoint','Unknown'],['Line tracking','No sensor endpoint','Unknown'],['Battery','No telemetry source','Unknown'],['Measured speed','No encoder feedback','Unknown']].map(([name, detail, value]) => <article className="card sensor-card" key={name}><div className="sensor-icon"><Activity size={18}/></div><span className="eyebrow">{name}</span><strong>{value}</strong><p>{detail}</p><span className="data-tag unknown-tag">Not reported</span></article>)}
        <article className="card diagnostic-summary"><div className="card-head"><div><h2>Hardware diagnostics</h2><p>API capability does not confirm physical operation.</p></div><button className="text-button" onClick={() => navigate('settings')}>Details <ArrowRight size={15}/></button></div><div className="status-rows"><div><span>Library</span><b>{known(diagnostics?.library_installed, 'Installed', 'Not installed')}</b></div><div><span>Driver initialization</span><b>{known(diagnostics?.driver_initialized, 'Initialized; hardware untested', 'Not initialized')}</b></div><div><span>Last error</span><b className={diagnostics?.last_error ? 'diagnostic-error' : ''}>{diagnostics?.last_error ?? 'Unknown'}</b></div></div></article>
      </section></>}
      {tab === 'logs' && <section className="placeholder card"><div className="placeholder-icon"><Waypoints/></div><div className="eyebrow">DRIVE HISTORY</div><h1>No drive records yet</h1><p>This version does not store drive sessions or experiment data.</p><span className="badge-muted">No recorded data</span></section>}
      {tab === 'settings' && <><section className="page-heading"><div><div className="eyebrow">PREFERENCES & DIAGNOSTICS</div><h1>Settings</h1><p>Connection, control preferences and hardware diagnostics.</p></div><button className="secondary theme-button" onClick={() => setDark(!dark)}>{dark ? <Sun size={16}/> : <Moon size={16}/>} {dark ? 'Light mode' : 'Dark mode'}</button></section><section className="settings-grid">
        <div className="card settings-card"><h2>Server connection</h2><p>Connect to the Raspberry Pi on the same Wi-Fi network.</p><label className="field-label" htmlFor="backend-url">Backend URL</label><div className="input-row"><input id="backend-url" inputMode="url" autoCapitalize="none" value={host} onChange={e => setHost(e.target.value)} placeholder="http://192.168.0.20:8000"/><button className="primary" onClick={() => void connect()}>Test connection</button></div><div className="connection-line"><span className={connected ? 'dot online' : 'dot'}/>{connected ? 'Server is reachable.' : 'Saved address: ' + (localStorage.getItem('picarx-host') || 'not set')}</div><button className="text-button disconnect-button" onClick={async () => { await stop(); setConnected(false); setStatus(null); }}>Disconnect and stop <CircleStop size={15}/></button></div>
        <div className="card settings-card diagnostic-card"><h2>Hardware diagnostics</h2><p>API availability is separate from physical device operation.</p><dl className="diagnostic-list">
          <div><dt>Raspberry Pi server</dt><dd>{connected ? 'Connected' : 'Unavailable'}</dd></div><div><dt>PiCar-X library</dt><dd>{known(diagnostics?.library_installed, 'Installed', 'Not installed')}</dd></div><div><dt>Library version</dt><dd>{diagnostics?.library_version ?? 'Unknown / not declared'}</dd></div><div><dt>API compatibility</dt><dd>{known(diagnostics?.api_compatible, 'Compatible', 'Incompatible')}</dd></div><div><dt>Driver initialized</dt><dd>{known(diagnostics?.driver_initialized, 'Initialized; physical operation untested', 'Not initialized')}</dd></div><div><dt>Motor API</dt><dd>{known(diagnostics?.motor_control_available, 'Available; physical operation untested', 'Unavailable')}</dd></div><div><dt>Steering API</dt><dd>{known(diagnostics?.steering_servo_available, 'Available; physical operation untested', 'Unavailable')}</dd></div><div><dt>Camera device</dt><dd>{known(diagnostics?.camera_device_available, 'Detected; image untested', 'Not detected')}</dd></div><div><dt>Run mode</dt><dd>{runMode ?? 'Unknown'}</dd></div><div><dt>Last error</dt><dd className={diagnostics?.last_error ? 'diagnostic-error' : ''}>{diagnostics?.last_error ?? status?.fault ?? 'Unknown'}</dd></div>
        </dl></div>
        <div className="card settings-card"><h2>Driving controls</h2><p>These values limit commands sent to the backend. They are not measured vehicle speed.</p><label className="field-label">Forward PWM limit: {forwardSpeed}%</label><input aria-label="Forward PWM limit" type="range" min="10" max="60" value={forwardSpeed} onChange={e => setForwardSpeed(+e.target.value)}/><label className="field-label">Reverse PWM limit: {reverseSpeed}%</label><input aria-label="Reverse PWM limit" type="range" min="10" max="60" value={reverseSpeed} onChange={e => setReverseSpeed(+e.target.value)}/><label className="field-label">Steering angle limit: {steering}?</label><input aria-label="Steering angle limit" type="range" min="10" max="30" value={steering} onChange={e => setSteering(+e.target.value)}/></div>
        <div className="card settings-card"><h2>Steering calibration</h2><p>Center offset is saved by the backend. Tests are sent only when requested.</p><div className="calibration-value"><span>Center offset</span><b>{status?.calibration ?? 0}?</b></div><input aria-label="Steering calibration" type="range" min="-20" max="20" value={status?.calibration ?? 0} onChange={async e => { try { await request('/api/settings/calibration', { method: 'PUT', body: JSON.stringify({ steering_center: +e.target.value }) }); await refresh(); } catch { setError('Failed to save calibration.'); } }}/><div className="button-row"><button className="secondary" onClick={async () => { try { await request('/api/settings/calibration', { method: 'PUT', body: JSON.stringify({ steering_center: 0 }) }); await refresh(); } catch { setError('Could not restore the default.'); } }}><RotateCcw size={14}/> Restore default</button><span className="helper">?20? to +20?</span></div><div className="button-row"><button className="secondary" onClick={async () => { try { await request('/api/settings/steering-test', { method: 'POST', body: JSON.stringify({ direction: 'left', angle: 15 }) }); await refresh(); } catch { setError('Left steering test failed.'); } }}>Test left</button><button className="secondary" onClick={async () => { try { await request('/api/settings/steering-test', { method: 'POST', body: JSON.stringify({ direction: 'center' }) }); await refresh(); } catch { setError('Center steering failed.'); } }}>Center</button><button className="secondary" onClick={async () => { try { await request('/api/settings/steering-test', { method: 'POST', body: JSON.stringify({ direction: 'right', angle: 15 }) }); await refresh(); } catch { setError('Right steering test failed.'); } }}>Test right</button></div></div>
        <div className="card settings-card"><h2>Camera servo calibration</h2><p>Calibration values are configurable. No stream or live servo movement is implied.</p><label className="field-label">Pan center: {status?.pan_calibration ?? 0}?</label><input aria-label="Camera pan center" type="range" min="-30" max="30" value={status?.pan_calibration ?? 0} onChange={async e => { try { await request('/api/settings/calibration', { method: 'PUT', body: JSON.stringify({ pan_center: +e.target.value }) }); await refresh(); } catch { setError('Pan calibration failed.'); } }}/><label className="field-label">Tilt center: {status?.tilt_calibration ?? 0}?</label><input aria-label="Camera tilt center" type="range" min="-30" max="30" value={status?.tilt_calibration ?? 0} onChange={async e => { try { await request('/api/settings/calibration', { method: 'PUT', body: JSON.stringify({ tilt_center: +e.target.value }) }); await refresh(); } catch { setError('Tilt calibration failed.'); } }}/><button className="secondary" onClick={async () => { try { await request('/api/settings/calibration', { method: 'PUT', body: JSON.stringify({ pan_center: 0, tilt_center: 0 }) }); await refresh(); } catch { setError('Could not restore camera centers.'); } }}><RotateCcw size={14}/> Restore camera centers</button></div>
        <div className="card settings-card app-info"><h2>Application</h2><div className="status-rows"><div><span>Version</span><b>0.1.0</b></div><div><span>Backend mode</span><b>{runMode ?? 'Unknown'}</b></div><div><span>Watchdog</span><b>{status?.watchdog_ms != null ? `${status.watchdog_ms} ms` : 'Unknown'}</b></div></div><div className="warning">Hardware API availability does not verify that motors, steering or the camera work correctly.</div></div>
      </section></>}
      <footer>PiCar X Control <span>?</span> Local network <span>?</span> Backend safety watchdog</footer>
    </main>
    {!driveMode && <nav className="mobile-nav">{tabs.map(item => <button className={tab === item.id ? 'selected' : ''} key={item.id} onClick={() => navigate(item.id)}><item.icon size={19}/><small>{item.label}</small></button>)}</nav>}
    {driveMode && <section className="drive-scene" aria-label="Drive mode">
      <div className="drive-video"><div className="video-empty"><div className="camera-empty-icon"><Camera size={26}/></div><b>Camera stream unavailable</b><span>No live video source is configured.</span></div></div>
      <header className="drive-hud"><div className="hud-brand"><span className={connected ? 'dot online' : 'dot'}/><div><b>{connected ? 'Connected' : 'Connection lost'}</b><small>{runMode === 'mock' ? 'MOCK MODE' : runMode === 'hardware' ? 'HARDWARE' : 'MODE UNKNOWN'}</small></div></div><div className="hud-motion"><span className={status?.fault || !connected ? 'hud-error' : ''}>{motionLabel}</span><b>{connected && status ? `${status.speed}%` : '?'}<small> PWM</small></b></div><button className="hud-settings" aria-label="Open settings" onClick={() => navigate('settings')}><Settings size={18}/></button><button className="exit-drive" onClick={leaveDrive}><X size={18}/> Exit</button></header>
      <div className="drive-controls"><div className="joystick-column"><span className="control-caption">STEER</span><div className="game-stick" onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); updateStick(e, setStick, beginDrive, () => { if (activeDirection.current) void stop(); }); }} onPointerMove={e => { if (e.currentTarget.hasPointerCapture(e.pointerId)) updateStick(e, setStick, beginDrive, () => { if (activeDirection.current) void stop(); }); }} onPointerUp={() => { setStick({ x: 0, y: 0 }); void stop(); }} onPointerCancel={() => { setStick({ x: 0, y: 0 }); void stop(); }} onLostPointerCapture={() => { setStick({ x: 0, y: 0 }); if (activeDirection.current) void stop(); }}><div className="stick-ring"/><div className="game-stick-handle" style={{ transform: `translate(${stick.x}px, ${stick.y}px)` }}><Joystick size={25}/></div></div><small>Drag to steer ? center to stop</small></div>
        <div className="drive-center"><div className="motion-chip"><span className={connected ? 'dot online' : 'dot'}/>{motionLabel}</div><div className="drive-metrics"><div><small>STEERING</small><b>{status?.steering ?? '?'}?</b></div><div><small>COMMAND PWM</small><b>{status?.speed ?? '?'}%</b></div><div><small>MEASURED SPEED</small><b>Unknown</b></div></div><div className="drive-speed"><span><SlidersHorizontal size={14}/> Steering limit <b>{steering}?</b></span><input aria-label="Drive steering angle limit" type="range" min="10" max="30" value={steering} onChange={e => setSteering(+e.target.value)}/><span><Gauge size={15}/> Forward PWM <b>{forwardSpeed}%</b></span><input aria-label="Drive forward PWM limit" type="range" min="10" max="60" value={forwardSpeed} onChange={e => setForwardSpeed(+e.target.value)}/><span><ArrowDown size={14}/> Reverse PWM <b>{reverseSpeed}%</b></span><input aria-label="Drive reverse PWM limit" type="range" min="10" max="60" value={reverseSpeed} onChange={e => setReverseSpeed(+e.target.value)}/></div></div>
        <div className="pedals"><button className="pedal reverse" onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); beginDrive('backward'); }} onPointerUp={() => void stop()} onPointerCancel={() => void stop()} onLostPointerCapture={() => { if (activeDirection.current) void stop(); }}><ArrowDown size={22}/><span>REVERSE</span><small>{reverseSpeed}% PWM</small></button><button className="pedal forward" onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); beginDrive('forward'); }} onPointerUp={() => void stop()} onPointerCancel={() => void stop()} onLostPointerCapture={() => { if (activeDirection.current) void stop(); }}><ArrowUp size={22}/><span>FORWARD</span><small>{forwardSpeed}% PWM</small></button><button className="emergency drive-emergency" onPointerDown={e => e.preventDefault()} onClick={() => void stop()}><CircleStop size={19}/><span>EMERGENCY STOP</span></button></div>
      </div>
      <div className="rotate-hint"><RotateCcw size={16}/> For a wider view, rotate your phone to landscape. Controls remain available in portrait.</div>
    </section>}
  </div>;
  return app;
}

function updateStick(e: React.PointerEvent<HTMLDivElement>, setStick: (value: { x: number; y: number }) => void, beginDrive: (direction: DriveDirection) => void, stop: () => void) {
  const rect = e.currentTarget.getBoundingClientRect();
  const dx = e.clientX - rect.left - rect.width / 2;
  const dy = e.clientY - rect.top - rect.height / 2;
  const radius = rect.width * 0.34;
  const distance = Math.hypot(dx, dy);
  const scale = distance > radius ? radius / distance : 1;
  const x = dx * scale, y = dy * scale;
  setStick({ x, y });
  if (distance < rect.width * 0.16) { stop(); return; }
  beginDrive(Math.abs(x) > Math.abs(y) ? (x < 0 ? 'left' : 'right') : (y < 0 ? 'forward' : 'backward'));
}
