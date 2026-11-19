"""The adapter is off by default and never embeds device configuration."""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

DEVICE=pathlib.Path(__file__).resolve().parents[1]/'device'
spec=importlib.util.spec_from_file_location('integration', DEVICE/'integrate_legacy.py')
integration=importlib.util.module_from_spec(spec);spec.loader.exec_module(integration)

SYNTHETIC='''#!/bin/sh
# kindle-dash v2:
TOUCH_MAX_PER_DAY=1
listen_allowed() { :; }
wait_until() { :; }
is_quiet() { :; }
touch_on() { :; }
suspend_until() { :; }
# ---------------------------------------------------------------- fetch / draw
fetch() {
 :
}
draw() {
 :
}
update_zones() {
 :
}
on_tap() {
 :
}
case "$1" in
  send) send_main "$2"; exit 0 ;;
esac
  charging && TMP="$TMP" FBINK="$FBINK" DASH_URL="$DASH_URL" ZONES_URL="$ZONES_URL" setsid sh /mnt/us/dashboard/local/dinner.sh prefetch </dev/null >/dev/null 2>&1 &
'''

class Tests(unittest.TestCase):
    def test_patch_is_opt_in_and_syntax_valid(self):
        value=integration.patch(SYNTHETIC)
        self.assertIn('IH_DEVICE_ENABLED=false',value)
        self.assertIn('ih_enabled && return 0',value)
        self.assertIn('ih_run refresh main',value)
        self.assertIn('ih_sync_zones',value)
        result=subprocess.run(['sh','-n'],input=value,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('suspend_until() { :; }',value)
        self.assertIn('listen_allowed() { :; }',value)
    def test_refuses_unknown_or_already_patched_source(self):
        with self.assertRaises(ValueError):integration.patch('unexpected output '+SYNTHETIC)
        with self.assertRaises(ValueError):integration.patch(SYNTHETIC.replace('on_tap() {','tap_unknown() {'))
        with self.assertRaises(ValueError):integration.patch(integration.patch(SYNTHETIC))
    def test_generated_copy_is_private_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=pathlib.Path(tmp)/'source';output=pathlib.Path(tmp)/'output';source.write_text(SYNTHETIC)
            cmd=[sys.executable,str(DEVICE/'integrate_legacy.py'),str(source),str(output)]
            subprocess.run(cmd,check=True,capture_output=True)
            self.assertEqual(output.stat().st_mode & 0o777,0o600)
            self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode,0)
    def test_sync_filters_mains_when_physically_on_battery(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=pathlib.Path(tmp);(base/'ih-device').mkdir();(base/'state').mkdir()
            (base/'ih-device'/'env.sh').write_text('IH_DEVICE_ENABLED=true\nIH_DEVICE_DIR="'+tmp+'/state"\n')
            (base/'state'/'current').write_text('f'*32+'\n')
            (base/'state'/'zones').write_text('a'*32+' 1 2 3 4 battery none\n'+'b'*32+' 5 6 7 8 mains home\n')
            shell='BASE="$1"; TMP="$1"; POWER="$2"; charging() { [ "$POWER" = mains ]; }; '+integration.BOOT+'\nih_sync_zones\n'
            for state,count in (('battery',1),('mains',2)):
                subprocess.run(['sh','-c',shell,'sh',tmp,state],check=True)
                self.assertEqual(len((base/'zones').read_text().splitlines()),count)
            (base/'state'/'current').unlink()
            subprocess.run(['sh','-c',shell,'sh',tmp,'mains'],check=True)
            self.assertEqual((base/'zones').read_text(),'')
    def test_draw_cadence_and_failure_preserve_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=pathlib.Path(tmp)
            env=dict(os.environ,IH_DEVICE_DIR=tmp,IH_DEVICE_CHARGING='false',FULL_REFRESH_EVERY='2',IH_EIPS='/bin/true')
            for count in (1,2,1):
                subprocess.run(['sh',str(DEVICE/'ih-draw.sh'),'fixture.png'],env=env,check=True)
                self.assertEqual(int((base/'draw-count').read_text()),count)
            env['IH_EIPS']='/bin/false'
            self.assertNotEqual(subprocess.run(['sh',str(DEVICE/'ih-draw.sh'),'fixture.png'],env=env).returncode,0)
            self.assertEqual(int((base/'draw-count').read_text()),1)
    def test_remote_tap_wakes_wifi_but_local_back_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=pathlib.Path(tmp);(base/'ih-device').mkdir();(base/'state').mkdir()
            (base/'ih-device'/'env.sh').write_text('IH_DEVICE_ENABLED=true\nIH_DEVICE_DIR="'+tmp+'/state"\n')
            (base/'state'/'screen').write_text('main\n')
            shell='''BASE="$1"; TMP="$1"; TOUCH_REFETCH=45; charging() { return 1; }
lipc-get-prop() { echo 0; }; lipc-set-prop() { echo wake >> "$BASE/log"; }
wait_wifi() { echo wait >> "$BASE/log"; return 0; }; status_line() { :; }
now() { echo 1000; }; '''+integration.BOOT+'''
ih_run() { echo "$*" >> "$BASE/log"; return 0; }
ih_send "$2"
'''
            subprocess.run(['sh','-c',shell,'sh',tmp,'a'*32],check=True)
            self.assertEqual((base/'log').read_text().splitlines(),['wake','wait','tap '+'a'*32])
            (base/'log').unlink()
            subprocess.run(['sh','-c',shell,'sh',tmp,'cache_back'],check=True)
            self.assertEqual((base/'log').read_text().splitlines(),['tap cache_back'])

if __name__=='__main__':unittest.main()
