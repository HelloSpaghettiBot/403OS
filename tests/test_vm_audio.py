"""Validate recorded PCM evidence using regular-file fixtures; no VM or audio devices."""
import hashlib
import importlib.machinery
import importlib.util
import math
from pathlib import Path
import struct
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('surface_vm_audio_fixture',str(ROOT/'scripts/vm.py'))
spec=importlib.util.spec_from_loader(loader.name,loader)
vm=importlib.util.module_from_spec(spec);loader.exec_module(vm)


class CaptureEvidence(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'capture.wav'
        self.pcm=b''.join(struct.pack('<hh',int(1000*math.sin(2*math.pi*440*i/44100)),
                                   int(1000*math.sin(2*math.pi*440*i/44100))) for i in range(44100))

    def recording(self,pcm=None,zero_sizes=False):
        pcm=self.pcm if pcm is None else pcm
        header=struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',0 if zero_sizes else len(pcm)+36,
                           b'WAVE',b'fmt ',16,1,2,44100,176400,4,16,b'data',0 if zero_sizes else len(pcm))
        return header+pcm

    def test_finalized_and_exact_qemu_zero_size_headers_keep_real_samples_and_raw_file(self):
        for zero_sizes in (False,True):
            with self.subTest(zero_sizes=zero_sizes):
                raw=self.recording(zero_sizes=zero_sizes);self.path.write_bytes(raw)
                capture=vm.inspect_audio_capture(self.path);vm.require_audio_evidence(capture,True)
                self.assertEqual(capture['frames'],44100);self.assertEqual(capture['duration_seconds'],1)
                self.assertGreaterEqual(capture['peak'],999);self.assertGreater(capture['signal_frames'],30000)
                self.assertEqual(capture['header_finalized'],not zero_sizes)
                self.assertEqual(capture['sha256'],hashlib.sha256(raw).hexdigest())
                self.assertEqual(self.path.read_bytes(),raw)

    def test_silence_and_single_click_never_count_as_playback(self):
        for pcm in (bytes(len(self.pcm)),struct.pack('<hh',2000,2000)+bytes(len(self.pcm)-4)):
            for zero_sizes in (False,True):
                with self.subTest(peak=pcm[:4],zero_sizes=zero_sizes):
                    self.path.write_bytes(self.recording(pcm,zero_sizes))
                    capture=vm.inspect_audio_capture(self.path)
                    with self.assertRaisesRegex(RuntimeError,'non-silent'):vm.require_audio_evidence(capture,True)

    def test_samples_without_guest_alsa_success_do_not_pass(self):
        self.path.write_bytes(self.recording());capture=vm.inspect_audio_capture(self.path)
        with self.assertRaisesRegex(RuntimeError,'ALSA'):vm.require_audio_evidence(capture,False)

    def test_truncation_partial_size_and_invalid_pcm_are_refused(self):
        valid=self.recording();zero=self.recording(zero_sizes=True)
        mutations=[valid[:20],valid[:-1],valid[:-4],valid+b'\0'*4,zero[:-1],
                   valid[:4]+bytes(4)+valid[8:],valid[:40]+bytes(4)+valid[44:]]
        for offset,fmt,value in ((16,'I',18),(20,'H',3),(22,'H',1),(24,'I',48000),
                                 (28,'I',1),(32,'H',2),(34,'H',8)):
            invalid=bytearray(zero);struct.pack_into('<'+fmt,invalid,offset,value);mutations.append(invalid)
        for raw in mutations:
            with self.subTest(length=len(raw),header=raw[:44]):
                self.path.write_bytes(raw)
                with self.assertRaises(RuntimeError):vm.inspect_audio_capture(self.path)

    def test_empty_short_long_and_symlink_captures_are_refused(self):
        for pcm in (b'',self.pcm[:100]):
            self.path.write_bytes(self.recording(pcm,zero_sizes=True))
            with self.assertRaisesRegex(RuntimeError,'duration'):vm.inspect_audio_capture(self.path)
        with self.path.open('wb') as output:output.truncate(128*1024*1024+1)
        with self.assertRaisesRegex(RuntimeError,'bounded'):vm.inspect_audio_capture(self.path)
        target=self.path.with_name('target.wav');target.write_bytes(self.recording())
        self.path.unlink();self.path.symlink_to(target)
        with self.assertRaises(OSError):vm.inspect_audio_capture(self.path)


if __name__=='__main__':unittest.main()
