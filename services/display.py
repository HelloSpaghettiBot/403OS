#!/usr/bin/python3
"""Guest-only DRM resolution settings and supervised Weston session.

Only modes advertised by connected DRM connectors are selectable. A change
remains in /run until confirmed; timeout, failed startup and reboot restore
the last confirmed setting (or the display's preferred mode).
"""
import argparse
import ctypes
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time

MARKER=Path('/etc/surface-image-id')
CONFIG=Path('/data/config/display.json')
RUNTIME=Path('/run/surface/display')
SERIAL=Path('/dev/ttyS0')
VM_DMI=Path('/sys/class/dmi/id/product_name')
TRIAL_SECONDS=45
MODE=re.compile(r'[1-9][0-9]{2,3}x[1-9][0-9]{2,3}')
OUTPUT=re.compile(r'[A-Za-z][A-Za-z0-9-]{0,63}')
_rotation_spec=importlib.util.spec_from_file_location('surface_orientation',Path(__file__).with_name('orientation.py'))
orientation=importlib.util.module_from_spec(_rotation_spec);_rotation_spec.loader.exec_module(orientation)


def guard():
    info=MARKER.lstat()
    if (os.geteuid()!=0 or not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or
        not MARKER.read_text().strip().startswith('surface-go-')):
        raise ValueError('Requires the privileged booted Surface OS image')
    if not any(len(row.split())>2 and row.split()[1:3]==['/data','ext4'] for row in Path('/proc/mounts').read_text().splitlines()):
        raise ValueError('Requires the separate persistent DATA filesystem')


def no_links(path):
    current=Path('/')
    for part in path.parts[1:]:
        current/=part
        try:info=current.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode):raise ValueError('Display state paths must not contain symlinks')


def private_directory(path):
    no_links(path);path.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('Display state directory must be private to its owner')


def read_json(path):
    no_links(path)
    try:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    except FileNotFoundError:return None
    with os.fdopen(descriptor,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>4096:
            raise ValueError('Unsafe or oversized display settings file')
        return json.loads(source.read(4097))


def write_json(path,value):
    private_directory(path.parent)
    temporary=path.with_name('.'+path.name+'-'+secrets.token_hex(6))
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        with os.fdopen(descriptor,'w') as output:
            json.dump(value,output);output.write('\n');output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:temporary.unlink(missing_ok=True)


class Connector(ctypes.Structure):
    # Stable xf86drmMode.h prefixes; no modesetting ioctl is ever invoked here.
    _fields_=[('connector_id',ctypes.c_uint32),('encoder_id',ctypes.c_uint32)]


class Encoder(ctypes.Structure):
    _fields_=[('encoder_id',ctypes.c_uint32),('encoder_type',ctypes.c_uint32),('crtc_id',ctypes.c_uint32)]


class Crtc(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint32) for name in ('crtc_id','buffer_id','x','y','width','height')]+[('mode_valid',ctypes.c_int)]


def drm_library():
    library=ctypes.CDLL('libdrm.so.2',use_errno=True)
    for name,kind in (('ConnectorCurrent',Connector),('Encoder',Encoder),('Crtc',Crtc)):
        get=getattr(library,'drmModeGet'+name);get.argtypes=[ctypes.c_int,ctypes.c_uint32];get.restype=ctypes.POINTER(kind)
        free=getattr(library,'drmModeFree'+name.replace('Current',''));free.argtypes=[ctypes.POINTER(kind)];free.restype=None
    return library


def current_mode(library,card,connector_id):
    descriptor=os.open(card,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):raise ValueError('DRM card is not a character device')
        connector=library.drmModeGetConnectorCurrent(descriptor,connector_id)
        if not connector:return ''
        try:
            if not connector.contents.encoder_id:return ''
            encoder=library.drmModeGetEncoder(descriptor,connector.contents.encoder_id)
            if not encoder:return ''
            try:
                if not encoder.contents.crtc_id:return ''
                crtc=library.drmModeGetCrtc(descriptor,encoder.contents.crtc_id)
                if not crtc:return ''
                try:return f'{crtc.contents.width}x{crtc.contents.height}' if crtc.contents.mode_valid else ''
                finally:library.drmModeFreeCrtc(crtc)
            finally:library.drmModeFreeEncoder(encoder)
        finally:library.drmModeFreeConnector(connector)
    finally:os.close(descriptor)


def inventory(sys_drm=Path('/sys/class/drm'),dev_dri=Path('/dev/dri')):
    library=drm_library();outputs=[]
    for node in sorted(sys_drm.glob('card*-*')):
        match=re.fullmatch(r'(card[0-9]+)-([A-Za-z][A-Za-z0-9-]{0,63})',node.name)
        if not match or (node/'status').read_text().strip()!='connected':continue
        modes=[]
        raw=(node/'modes').read_text()
        if len(raw)>65536:raise ValueError('DRM mode inventory exceeded its bound')
        for mode in raw.splitlines():
            if MODE.fullmatch(mode) and mode not in modes:modes.append(mode)
        if not modes:continue
        try:current=current_mode(library,dev_dri/match[1],int((node/'connector_id').read_text().strip()))
        except OSError:current=''
        outputs.append(dict(name=match[2],card=match[1],modes=['preferred',*modes],current=current))
        if len(outputs)>16:raise ValueError('Too many connected DRM outputs')
    # Weston output names must identify one unambiguous physical connector.
    names=[output['name'] for output in outputs]
    if len(names)!=len(set(names)):raise ValueError('Connected DRM output names are ambiguous across cards')
    return outputs


def selection(value,outputs):
    if value is None:return None
    if not isinstance(value,dict) or not OUTPUT.fullmatch(str(value.get('output',''))) or not isinstance(value.get('mode'),str):
        raise ValueError('Invalid display selection')
    if value['mode']!='preferred' and not MODE.fullmatch(value['mode']):raise ValueError('Invalid display mode')
    for output in outputs:
        if output['name']==value['output'] and value['mode'] in output['modes']:
            return dict(output=value['output'],mode=value['mode'])
    return None


class Settings:
    def __init__(self,config=CONFIG,runtime=RUNTIME):
        self.config=config;self.runtime=runtime;self.pending=runtime/'pending.json';self.failure=runtime/'fallback.json'
        self.rotation_config=config.with_name('rotation.json');self.rotation_state=runtime/'rotation.json'
    def rotation_preference(self):
        try:value=read_json(self.rotation_config)
        except (ValueError,OSError):value=None
        if not isinstance(value,dict) or type(value.get('enabled')) is not bool:return dict(enabled=True,locked_transform='normal')
        locked=value.get('locked_transform','normal')
        return dict(enabled=value['enabled'],locked_transform=locked if locked in orientation.TRANSFORMS.values() else 'normal')
    def rotation_status(self):
        preference=self.rotation_preference()
        try:state=read_json(self.rotation_state)
        except (ValueError,OSError):state=None
        if not isinstance(state,dict):state={}
        return dict(rotation_enabled=preference['enabled'],rotation_available=bool(state.get('available',False)),
                    rotation_transform=state.get('transform','normal'),rotation_orientation=state.get('orientation','normal'),
                    rotation_sensor=state.get('sensor',''),rotation_matrix_source=state.get('matrix_source',''),
                    rotation_suspended=bool(state.get('suspended',False)),
                    rotation_message=state.get('message','Waiting for the display accelerometer supervisor.'))
    def set_rotation(self,enabled):
        status=self.rotation_status()
        write_json(self.rotation_config,dict(enabled=enabled,locked_transform=status['rotation_transform']))
        return dict(event='rotation-preference',rotation_enabled=enabled,
                    message='Automatic screen rotation enabled.' if enabled else 'Automatic rotation disabled; current orientation is locked.')
    def saved(self,outputs):
        try:return selection(read_json(self.config),outputs)
        except (ValueError,OSError):return None
    def trial(self,outputs,now=None):
        now=time.monotonic() if now is None else now
        try:value=read_json(self.pending)
        except (ValueError,OSError):return None
        if (not isinstance(value,dict) or type(value.get('deadline')) not in (int,float) or
            not now<value['deadline']<=now+TRIAL_SECONDS+1 or selection(value,outputs) is None):return None
        return value
    def active(self,outputs,now=None):
        trial=self.trial(outputs,now)
        return selection(trial,outputs) if trial else self.saved(outputs)
    def status(self,outputs):
        now=time.monotonic();trial=self.trial(outputs,now)
        fallback=read_json(self.failure)
        return dict(event='display-status',outputs=outputs,saved=self.saved(outputs),pending=trial,**self.rotation_status(),
                    trial_active=bool(trial),trial_output=trial['output'] if trial else '',trial_mode=trial['mode'] if trial else '',
                    remaining_seconds=max(0,int(trial['deadline']-now+.999)) if trial else 0,
                    message='Keep this resolution within 45 seconds or it will revert.' if trial else
                    fallback.get('message','') if isinstance(fallback,dict) else 'Choose a connected display and an advertised resolution.')
    def set(self,output,mode,outputs):
        value=selection(dict(output=output,mode=mode),outputs)
        if value is None:raise ValueError('That output or resolution is not currently advertised by DRM')
        write_json(self.pending,dict(value,deadline=time.monotonic()+TRIAL_SECONDS))
        self.failure.unlink(missing_ok=True)
        return dict(event='display-trial',message='Applying resolution. HOME will restart; keep the new mode within 45 seconds.',**value)
    def confirm(self,outputs):
        trial=self.trial(outputs)
        if trial is None:raise ValueError('There is no unexpired resolution trial to keep')
        active=next(output['current'] for output in outputs if output['name']==trial['output'])
        if not active or (trial['mode']!='preferred' and active!=trial['mode']):
            raise ValueError('DRM has not confirmed that the requested resolution is active')
        write_json(self.config,selection(trial,outputs));self.pending.unlink(missing_ok=True)
        return dict(event='display-confirmed',message='Resolution saved for this device and future boots.')
    def revert(self,message='Resolution restored to the last confirmed setting.'):
        self.pending.unlink(missing_ok=True);write_json(self.failure,dict(message=message))
        return dict(event='display-reverted',message=message)


class Rotation:
    def __init__(self,settings):
        self.settings=settings;self.worker=orientation.SensorWorker();self.filter=orientation.Debounce()
        self.current='normal';self.output='';self.sample={};self.sample_at=-100.;self.retry=0.;self.last_state=None
    def reset_compositor(self):
        self.current='normal';self.output='';self.retry=0.;self.filter.reset()
    def update(self,outputs,trial,now=None):
        now=time.monotonic() if now is None else now
        preference=self.settings.rotation_preference();output=orientation.internal_output(outputs)
        enabled=preference['enabled'];message='Automatic rotation is disabled; the current orientation is locked.'
        suspended=bool(trial);available=False
        if enabled and output:
            try:value=self.worker.poll(now)
            except (OSError,ValueError) as error:value=dict(available=False,message=str(error))
            if value is not None:self.sample=value;self.sample_at=now
            available=bool(self.sample.get('available',False)) and now-self.sample_at<2.5
            message=self.sample.get('message','Waiting for stable accelerometer readings.')
            if self.sample.get('available') and not available:message='Waiting for a fresh accelerometer reading; orientation is unchanged.'
            if suspended:
                self.filter.reset();message='Automatic rotation is paused until the resolution trial is kept or reverted.'
            elif available:
                desired=self.filter.update(self.sample['vector'],self.current,now) if value is not None else None
                if not orientation.MODULE.is_file():
                    desired=None;available=False;message='The Weston live rotation module is not installed.'
                elif desired and now>=self.retry:
                    try:
                        orientation.apply_transform(output,desired)
                        self.current=desired;self.output=output;message='Screen orientation updated without restarting applications.'
                    except (OSError,ValueError) as error:
                        message=str(error);available=False;self.retry=now+10;self.filter.reset()
                elif now<self.retry:available=False;message='Live rotation is unavailable; retrying after the compositor cooldown.'
                else:message='Automatic screen rotation is monitoring the accelerometer.'
            else:self.filter.reset()
        else:
            self.worker.stop();self.filter.reset()
            if not output:message='No unambiguous internal display is connected; automatic rotation is inactive.'
            elif not trial and preference['locked_transform']!=self.current and now>=self.retry:
                try:
                    orientation.apply_transform(output,preference['locked_transform']);self.current=preference['locked_transform'];self.output=output
                except (OSError,ValueError) as error:message=str(error);self.retry=now+10
        label=next((label for label,transform in orientation.TRANSFORMS.items() if transform==self.current),'normal')
        state=dict(available=available,transform=self.current,orientation=label,suspended=suspended,
                   sensor=self.sample.get('sensor',''),matrix_source=self.sample.get('matrix_source',''),message=message)
        if state!=self.last_state:write_json(self.settings.rotation_state,state);self.last_state=state
    def close(self):self.worker.stop()


def weston_config(outputs,chosen):
    content='[core]\nshell=kiosk-shell.so\nidle-time=0\nxwayland=true\nrequire-input=false\n\n[keyboard]\nkeymap_layout=us\n'
    for output in outputs:
        mode=chosen['mode'] if chosen and chosen['output']==output['name'] else 'preferred'
        content+=f'\n[output]\nname={output["name"]}\nmode={mode}\n'
    return content


def terminate(process):
    if process is None:return
    if process.poll() is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    try:process.wait(timeout=7)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=2)


def session(settings,renderer):
    private_directory(settings.runtime)
    lock=os.open(settings.runtime/'session.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    running=[True]
    for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:running.__setitem__(0,False))
    compositor=shell=None;accepted=False;failures=0;display_failures=0;signature=None;rotation=Rotation(settings)
    runtime=settings.runtime.parent;socket=runtime/'wayland-0';config=runtime/'weston-display.ini'
    serial=SERIAL.open('ab',buffering=0)
    def pause(seconds):
        deadline=time.monotonic()+seconds
        while running[0] and time.monotonic()<deadline:
            time.sleep(min(.2,deadline-time.monotonic()))
    def recover(message):
        nonlocal compositor,shell,display_failures
        terminate(shell);shell=None;terminate(compositor);compositor=None
        display_failures=min(display_failures+1,5)
        delay=min(30,2**display_failures)
        print(message+f'; restarting display in {delay}s',flush=True)
        pause(delay)
    try:
        while running[0]:
            try: outputs=inventory()
            except OSError:
                recover('Display inventory temporarily unavailable');continue
            chosen=settings.active(outputs)
            desired=json.dumps(chosen,sort_keys=True)
            trial=settings.trial(outputs)
            if settings.pending.exists() and trial is None:
                settings.revert('Unconfirmed or unavailable resolution reverted automatically.')
            if compositor is None or desired!=signature:
                terminate(shell);shell=None;terminate(compositor);compositor=None
                if socket.is_socket():socket.unlink(missing_ok=True)
                config.write_text(weston_config(outputs,chosen));os.chmod(config,0o600)
                command=['weston','--backend=drm','--renderer='+renderer,'--xwayland',
                    '--socket=wayland-0','--config='+str(config),'--log=/data/logs/weston.log']
                if orientation.MODULE.is_file():command.append('--modules='+str(orientation.MODULE))
                try: compositor=subprocess.Popen(command,start_new_session=True)
                except OSError:
                    recover('Could not start the display process');continue
                rotation.reset_compositor()
                signature=desired
                ready=False
                for _ in range(100):
                    if not running[0] or compositor.poll() is not None:break
                    if socket.is_socket():ready=True;break
                    time.sleep(.1)
                if not ready:
                    terminate(compositor);compositor=None
                    if chosen:
                        if trial:settings.revert('The selected resolution could not start; restored the previous mode.')
                        else:settings.config.unlink(missing_ok=True);settings.revert('Saved mode could not start; using the preferred resolution.')
                        continue
                    recover('Display failed to start in the preferred mode');continue
                if not accepted:
                    if VM_DMI.read_text().strip()=='Surface Go OS Disposable Test VM':
                        subprocess.run(['/usr/sbin/surface-app-check'],check=False,stdout=serial,stderr=subprocess.STDOUT)
                    accepted=True
                failures=0;display_started=time.monotonic()
            if compositor.poll() is not None:
                if chosen:
                    terminate(shell);shell=None;terminate(compositor);compositor=None
                    if trial:settings.revert('Display process stopped; restored the previous resolution.')
                    else:settings.config.unlink(missing_ok=True)
                    continue
                recover('Display stopped unexpectedly');continue
            if time.monotonic()-display_started>60:display_failures=0
            rotation.update(outputs,trial)
            if shell is None:
                shell=subprocess.Popen(['/usr/bin/surface-shell'],start_new_session=True,stdout=serial,stderr=subprocess.STDOUT);shell_started=time.monotonic()
            elif shell.poll() is not None:
                duration=time.monotonic()-shell_started
                print(f'shell exited status={shell.returncode} duration={duration:.1f}',flush=True)
                failures=failures+1 if duration<10 else 0
                shell=None;pause(min(30,2**min(failures,5)))
            time.sleep(.2)
    finally:
        rotation.close();terminate(shell);terminate(compositor);serial.close();os.close(lock)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--status',action='store_true');group.add_argument('--set',nargs=2,metavar=('OUTPUT','MODE'))
    group.add_argument('--confirm',action='store_true');group.add_argument('--revert',action='store_true')
    group.add_argument('--session',action='store_true')
    group.add_argument('--autorotate',choices=('on','off'))
    group.add_argument('--rotation-monitor',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--renderer',choices=('gl','pixman'),default='pixman')
    args=parser.parse_args(argv)
    try:
        guard();settings=Settings()
        if args.rotation_monitor:orientation.monitor();return 0
        if args.session:session(settings,args.renderer);return 0
        private_directory(settings.runtime)
        lock=os.open(settings.runtime/'settings.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX)
            outputs=inventory()
            if args.autorotate:value=settings.set_rotation(args.autorotate=='on')
            elif args.status:value=settings.status(outputs)
            elif args.set:value=settings.set(*args.set,outputs)
            elif args.confirm:value=settings.confirm(outputs)
            else:value=settings.revert()
        finally:os.close(lock)
        print(json.dumps(value),flush=True);return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        print(json.dumps(dict(event='display-error',message=str(error))),flush=True);return 2


if __name__=='__main__':raise SystemExit(main())
