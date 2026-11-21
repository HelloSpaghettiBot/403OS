"""Exercise acceptance evidence validation without opening host apps/services."""
import hashlib
import contextlib
import importlib.machinery
import importlib.util
import json
import io
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import wave

ROOT=Path(__file__).resolve().parents[1]
HELPER=ROOT/'external/board/surface-go1/overlay/usr/sbin/surface-app-check'
loader=importlib.machinery.SourceFileLoader('surface_app_check',str(HELPER))
spec=importlib.util.spec_from_loader(loader.name,loader)
check=importlib.util.module_from_spec(spec)
loader.exec_module(check)


class PolicyCommands:
    """In-memory ip fixture. It never runs a subprocess or touches networking."""
    def __init__(self,scenario='success'):
        self.scenario=scenario;self.calls=[];self.deleted=[];self.added=[];self.emitted=[];self.fail_delete=0
        self.rules={4:[],6:[]};self.routes={4:{},6:{}};self.readback_failed=False
        self.names={'unspec':0,'default':253,'main':254,'local':255}
    def command(self,argv,log,timeout=10,env=None):
        self.calls.append(argv)
        if argv[0]!='/sbin/ip' or argv[1] not in ('-4','-6'):raise AssertionError('Unexpected non-fixture command')
        if not 0<timeout<=2:raise AssertionError('ip operation must be bounded')
        family=int(argv[1][1:]);args=argv[2:]
        if args==['rule','show']:
            if self.scenario=='malformed':return 0,'unrecognized rule format\n'
            if self.scenario=='readback-failed' and self.rules[family] and not self.readback_failed:
                self.readback_failed=True;raise RuntimeError('fixture rule readback timed out')
            if self.scenario=='foreign-collision' and self.added and not any(rule.get('foreign') for rule in self.rules[family]):
                own=self.added[-1]
                self.rules[family].append(dict(own,mark=own['mark']+1,foreign=True))
            result='0: from all lookup local\n32766: from all lookup main\n32767: from all lookup default\n'
            for rule in self.rules[family]:
                result+=f'{rule["priority"]}: from all fwmark {hex(rule["mark"])} lookup {rule["table"]}\n'
            return 0,result
        if args[:3]==['route','show','table']:
            table=int(args[3])
            if self.scenario=='missing-table' and family==6:return 2,'Error: ipv6: FIB table does not exist.\nDump terminated\n'
            if self.scenario=='route-failed':return 2,'RTNETLINK answers: Operation not supported\n'
            return 0,self.routes[family].get(table,'')
        raise AssertionError('Unexpected read-only fixture command')

    def exact_command(self,argv,log,timeout=10,env=None):
        args=argv[2:]
        if args[:2] not in (['rule','add'],['rule','del']):return self.command(argv,log,timeout,env)
        self.calls.append(argv)
        if argv[0]!='/sbin/ip' or argv[1] not in ('-4','-6') or not 0<timeout<=2:raise AssertionError('Invalid fixture command')
        if len(args)!=10 or args[2]!='priority' or args[4:7]!=['from','all','fwmark'] or args[8]!='lookup':
            raise AssertionError('A full exact selector is required; no broad delete or flush')
        mark,mask=args[7].split('/')
        if mask!='0xffffffff':raise AssertionError('Full mark mask required')
        family=int(argv[1][1:])
        rule=dict(priority=int(args[3]),table=int(args[9]),mark=int(mark,16),mask=0xffffffff)
        if args[1]=='add':
            self.added.append(dict(rule,family=family))
            if self.scenario=='add-failed' and family==6:return 2,'fixture unsupported IPv6 rule\n'
            self.rules[family].append(rule)
            if self.scenario=='add-timeout' and family==6:raise RuntimeError('fixture add timed out after apply')
            return 0,''
        self.deleted.append(dict(rule,family=family))
        if self.scenario=='delete-failed-once' and family==6 and self.fail_delete==0:
            self.fail_delete+=1;return 2,'fixture transient delete error\n'
        before=len(self.rules[family])
        self.rules[family]=[existing for existing in self.rules[family] if any(existing[key]!=rule[key] for key in ('priority','table','mark'))]
        return (0,'') if len(self.rules[family])<before else (2,'fixture rule not found\n')

class AcceptanceEvidence(unittest.TestCase):
    def test_host_entrypoint_refuses_before_work(self):
        if Path('/etc/surface-image-id').exists():self.skipTest('running inside the intended guest')
        run=subprocess.run(['python3',str(HELPER),'--components','browser'],capture_output=True,text=True,timeout=3)
        self.assertEqual(run.returncode,2);self.assertIn('SURFACE_APP_CHECK_REFUSED',run.stdout)

    def test_browser_nonce_and_javascript_endpoint(self):
        server=check.PageServer('fixture-nonce');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(base+'/rendered?token=wrong',timeout=2)
            refused.exception.close()
            self.assertFalse(server.rendered.is_set())
            with urllib.request.urlopen(base+'/acceptance?token=fixture-nonce',timeout=2) as response:
                page=response.read().decode()
            self.assertTrue(server.page_requested.is_set());self.assertFalse(server.rendered.is_set())
            self.assertIn('requestAnimationFrame',page);self.assertIn('/rendered?token=fixture-nonce',page)
            self.assertIn("fetch('https://buildroot.org/'",page);self.assertIn("mode:'no-cors'",page)
            self.assertIn("cache:'no-store'",page);self.assertIn('/tls-failure?token=fixture-nonce',page)
            with urllib.request.urlopen(base+'/rendered?token=fixture-nonce',timeout=2) as response:self.assertEqual(response.status,204)
            self.assertTrue(server.rendered.is_set())
            self.assertFalse(server.tls_success.is_set());self.assertFalse(server.tls_failure.is_set())
            for outcome in ('success','failure'):
                with self.assertRaises(urllib.error.HTTPError) as refused:
                    urllib.request.urlopen(base+'/tls-'+outcome+'?token=wrong',timeout=2)
                refused.exception.close()
                self.assertFalse(getattr(server,'tls_'+outcome).is_set())
                with urllib.request.urlopen(base+'/tls-'+outcome+'?token=fixture-nonce',timeout=2) as response:
                    self.assertEqual(response.status,204)
                self.assertTrue(getattr(server,'tls_'+outcome).is_set())
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_browser_cannot_pass_local_rendering_without_successful_tls(self):
        # Mock the guest browser process, not a real HTTPS request or host app.
        # This verifies evidence gating; only the guest run can prove TLS.
        for outcome in ('missing','failure','success','conflicting','rotation-failure'):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as temporary:
                server=SimpleNamespace(server_port=12345,nonce='fixture',serve_forever=lambda:None,
                                       shutdown=lambda:None,server_close=lambda:None)
                for name in ('page_requested','rendered','tls_success','tls_failure'):
                    setattr(server,name,threading.Event())
                server.page_requested.set();server.rendered.set()
                if outcome in ('success','conflicting','rotation-failure'):server.tls_success.set()
                if outcome in ('failure','conflicting'):server.tls_failure.set()
                process=SimpleNamespace(returncode=None,poll=lambda:None,wait=lambda timeout:0)
                clock=[0]
                def now():clock[0]+=5;return clock[0]
                with patch.object(Path,'is_socket',return_value=True),patch.object(Path,'is_file',return_value=True), \
                     patch.object(check,'PageServer',return_value=server),patch.object(check.subprocess,'Popen',return_value=process), \
                     patch.object(check,'wayland_committed',return_value=True),patch.object(check,'stop_group'), \
                     patch.object(check,'rotation_runtime_check',return_value={'status':'NOT_RUN'}) as rotation, \
                     patch.object(check.time,'monotonic',side_effect=now),patch.object(check.time,'sleep'), \
                     patch.object(check,'emit') as emitted:
                    if outcome=='success':
                        result=check.browser_check(Path(temporary),0)
                        self.assertEqual(result['tls']['status'],'PASS');self.assertTrue(result['tls']['certificate_validation'])
                        self.assertEqual(emitted.call_args_list[0].args[0],'SURFACE_BROWSER_DISPLAY_READY')
                    elif outcome=='rotation-failure':
                        rotation.side_effect=RuntimeError('live rotation was not verified')
                        with self.assertRaisesRegex(RuntimeError,'live rotation'):check.browser_check(Path(temporary),0)
                        self.assertEqual(emitted.call_args_list[0].args[0],'SURFACE_ROTATION_RUNTIME_FAIL')
                        self.assertNotIn('SURFACE_BROWSER_DISPLAY_READY',[call.args[0] for call in emitted.call_args_list])
                    else:
                        with self.assertRaisesRegex(RuntimeError,'HTTPS'):check.browser_check(Path(temporary),0)
                        emitted.assert_not_called();rotation.assert_not_called()

    def test_wayland_evidence_requires_real_surface_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            log=Path(temporary)/'browser.log'
            log.write_text('xdg_surface@2.get_toplevel(new id xdg_toplevel@3)\n')
            self.assertFalse(check.wayland_committed(log))
            log.write_text('xdg_surface@2.get_toplevel(new id xdg_toplevel@3)\nwl_surface@4.commit()\n')
            self.assertTrue(check.wayland_committed(log))

    def test_private_state_hash_cold_boot_and_identity_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=root/'tailscaled.state';proof=root/'checkpoint.json'
            state.write_text(json.dumps({'_machinekey':'fixture-private-value'}));state.chmod(0o600)
            first=check.inspect_state(state,proof,'boot-one');self.assertEqual(first['outcome'],'first_boot')
            self.assertEqual(first['file_sha256'],hashlib.sha256(state.read_bytes()).hexdigest())
            self.assertEqual(check.inspect_state(state,proof,'boot-one')['outcome'],'same_boot')
            self.assertEqual(check.inspect_state(state,proof,'boot-two')['outcome'],'identity_persisted')
            self.assertEqual(stat.S_IMODE(proof.stat().st_mode),0o600)
            self.assertNotIn('fixture-private-value',proof.read_text())
            state.write_text(json.dumps({'_machinekey':'changed-fixture'}))
            with self.assertRaises(RuntimeError):check.inspect_state(state,proof,'boot-three')
            state.chmod(0o644)
            with self.assertRaises(RuntimeError):check.inspect_state(state,proof,'boot-four')

    def test_unenrolled_empty_store_can_persist_without_login_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=root/'tailscaled.state';proof=root/'proof.json';state.write_text('{}');state.chmod(0o600)
            check.inspect_state(state,proof,'first')
            self.assertEqual(check.inspect_state(state,proof,'second')['outcome'],'store_persisted')

    def test_pcm_file_contains_real_sine_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio=Path(temporary)/'sine.wav';self.assertEqual(check.write_sine(audio),48000)
            with wave.open(str(audio),'rb') as source:
                self.assertEqual(source.getparams()[:4],(2,2,48000,48000));raw=source.readframes(48000)
            samples=struct.unpack('<'+'h'*(len(raw)//2),raw)
            self.assertGreater(max(samples),4000);self.assertLess(min(samples),-4000)
            self.assertEqual(samples[0::2],samples[1::2])

    def test_audio_logs_real_mixer_before_playback_and_refuses_mixer_failure(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(check,'guard'), \
             patch.object(Path,'is_file',return_value=True),patch.object(check.shutil,'which',return_value='/usr/bin/amixer'):
            root=Path(temporary)
            with patch.object(check,'command',side_effect=[(0,'fixture mixer'),(0,'')]) as command:
                result=check.audio_check(root)
                self.assertEqual(command.call_args_list[0].args[0],['/usr/bin/amixer','-c','0','contents'])
                self.assertEqual(command.call_args_list[1].args[0][0],'/usr/bin/aplay')
                self.assertIn('UNVERIFIED',result['audible_output'])
            with patch.object(check,'command',return_value=(1,'mixer failed')) as command:
                with self.assertRaisesRegex(RuntimeError,'mixer'):check.audio_check(root)
                self.assertEqual(command.call_count,1)
        with patch.object(check,'guard',side_effect=RuntimeError('host refused')), \
             patch.object(check,'command',side_effect=AssertionError('No host ALSA command may execute')):
            with self.assertRaisesRegex(RuntimeError,'host refused'):check.audio_check(Path('/unused'))

    def test_wine_pass_requires_real_probe_exit_and_explicit_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            valid=json.dumps(dict(event='wine-probe',exit=0,success=True,gameplay_verified=False))
            with patch.object(check,'command',side_effect=[(0,'libc.so.6 libGL.so.1'),(0,valid)]) as command:
                self.assertEqual(check.wine_check(root)['status'],'PASS')
                self.assertEqual(command.call_args.args[0],['/usr/bin/surface-windows-runner','--probe-wine'])
            for code,output in ((1,valid),(0,'Wine version 11.0'),(0,valid.replace('false','true'))):
                with self.subTest(code=code,output=output),patch.object(check,'command',side_effect=[(0,'libc.so.6 libGL.so.1'),(code,output)]):
                    with self.assertRaises(RuntimeError):check.wine_check(root)
            with patch.object(check,'command',return_value=(0,'no host libraries')) as command:
                with self.assertRaises(RuntimeError):check.wine_check(root)
                self.assertEqual(command.call_count,1)

    def policy_fixture(self,fixture):
        with tempfile.TemporaryDirectory() as temporary, patch.object(check,'guard'), \
             patch.object(check,'policy_table_names',return_value=fixture.names), \
             patch.object(check.secrets,'randbits',return_value=0x23456), \
             patch.object(check,'emit',side_effect=lambda marker,**fields: fixture.emitted.append((marker,fields))), \
             patch.object(check,'command',side_effect=fixture.exact_command):
            return check.tailscale_policy_check(Path(temporary))

    def test_policy_routing_requires_both_real_operations_and_removes_exact_rules(self):
        fixture=PolicyCommands();result=self.policy_fixture(fixture)
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['families'],['ipv4','ipv6'])
        self.assertTrue(result['temporary_rules_removed']);self.assertFalse(result['enrollment_verified'])
        self.assertEqual([rule['family'] for rule in fixture.added],[4,6])
        self.assertEqual([rule['family'] for rule in fixture.deleted],[6,4])
        self.assertFalse(fixture.rules[4]);self.assertFalse(fixture.rules[6])
        for rule in fixture.added:self.assertIn(rule,fixture.deleted)
        self.assertEqual(fixture.emitted[0][0],'SURFACE_TAILSCALE_POLICY_ROUTING_PASS')

    def test_policy_routing_skips_existing_priorities_named_tables_and_routes(self):
        fixture=PolicyCommands();fixture.names['owner_table']=181
        fixture.rules[4]=[dict(priority=52000,table=200,mark=123)]
        fixture.rules[6]=[dict(priority=100,table=181,mark=456)]
        fixture.routes[4][182]='10.4.3.0/24 dev fixture0\n'
        original={family:list(rules) for family,rules in fixture.rules.items()}
        self.policy_fixture(fixture)
        self.assertEqual([(rule['priority'],rule['table']) for rule in fixture.added],[(52003,183),(52003,183)])
        self.assertEqual(fixture.rules,original)

    def test_policy_routing_cleanup_on_add_readback_and_timeout_failures(self):
        for scenario in ('add-failed','readback-failed','add-timeout','delete-failed-once'):
            with self.subTest(scenario=scenario):
                fixture=PolicyCommands(scenario)
                with self.assertRaises(RuntimeError):self.policy_fixture(fixture)
                self.assertFalse(fixture.rules[4]);self.assertFalse(fixture.rules[6])
                self.assertTrue(fixture.deleted)
                for rule in fixture.deleted:self.assertIn(rule,fixture.added)
                self.assertFalse(fixture.emitted)

    def test_policy_cleanup_preserves_foreign_rules_with_same_priority_and_table(self):
        fixture=PolicyCommands('foreign-collision')
        with self.assertRaises(RuntimeError):self.policy_fixture(fixture)
        remaining=fixture.rules[4]+fixture.rules[6]
        self.assertTrue(remaining);self.assertTrue(all(rule.get('foreign') for rule in remaining))
        self.assertTrue(all(rule['mark']!=fixture.added[0]['mark'] for rule in remaining))

    def test_policy_routing_fails_before_mutation_for_ambiguous_inventory_or_collisions(self):
        for scenario in ('malformed','route-failed','all-occupied'):
            with self.subTest(scenario=scenario):
                fixture=PolicyCommands(scenario)
                if scenario=='all-occupied':fixture.names.update({f'owner_{index}':180+index for index in range(16)})
                with self.assertRaises(RuntimeError):self.policy_fixture(fixture)
                self.assertFalse(fixture.added);self.assertFalse(fixture.deleted)
        fixture=PolicyCommands('missing-table');self.assertEqual(self.policy_fixture(fixture)['status'],'PASS')

    def test_policy_routing_guard_precedes_every_ip_command_and_gates_daemon_check(self):
        with patch.object(check,'guard',side_effect=RuntimeError('guest refused')), \
             patch.object(check,'command',side_effect=AssertionError('Host ip must never execute')):
            with self.assertRaisesRegex(RuntimeError,'guest refused'):check.tailscale_policy_check(Path('/unused'))
        with patch.object(check,'tailscale_policy_check',side_effect=RuntimeError('IPv4 rules unavailable')), \
             patch.object(check,'command',side_effect=AssertionError('Daemon check must follow policy routing')):
            with self.assertRaisesRegex(RuntimeError,'IPv4 rules unavailable'):check.tailscale_check(Path('/unused'),'fixture-boot')

    def test_tailscale_waits_for_socket_after_idempotent_start_and_keeps_identity_checks(self):
        for correct_state in (True,False):
            with self.subTest(correct_state=correct_state),tempfile.TemporaryDirectory() as temporary:
                run=Path(temporary);ready=threading.Event()
                socket=Mock();socket.is_socket.side_effect=ready.is_set
                pid=Mock();pid.is_file.return_value=True;pid.read_text.return_value='123\n'
                proc=Mock();proc.read_bytes.return_value=(b'tailscaled\0--state=/data/tailscale/tailscaled.state\0'
                    b'--statedir=/data/tailscale\0' if correct_state else b'tailscaled\0--state=/tmp/wrong\0')
                checkpoint=run/'checkpoint.json'
                def path(value):
                    return {'/run/tailscale/tailscaled.pid':pid,'/run/tailscale/tailscaled.sock':socket,
                            '/proc/123/cmdline':proc,'/data/app-acceptance/tailscale-checkpoint.json':checkpoint}[value]
                process=Mock();process.communicate.return_value=(b'{"BackendState":"NeedsLogin"}',None)
                with patch.object(check,'Path',side_effect=path),patch.object(check,'tailscale_policy_check',return_value={'status':'PASS'}), \
                     patch.object(check,'command',return_value=(0,'')) as command, \
                     patch.object(check.time,'sleep',side_effect=lambda _:ready.set()), \
                     patch.object(check.subprocess,'Popen',return_value=process) as api, \
                     patch.object(check,'inspect_state',return_value={'outcome':'first_boot','file_sha256':'a'*64}), \
                     patch.object(check,'emit'),contextlib.redirect_stdout(io.StringIO()):
                    if correct_state:
                        result=check.tailscale_check(run,'fixture-boot')
                        self.assertEqual(result['backend'],'NeedsLogin');self.assertEqual(api.call_count,1)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'persistent state'):check.tailscale_check(run,'fixture-boot')
                        api.assert_not_called()
                    self.assertTrue(ready.is_set())
                    self.assertEqual(command.call_args.args[0],['/etc/init.d/S71tailscaled','start'])

if __name__=='__main__':unittest.main()
