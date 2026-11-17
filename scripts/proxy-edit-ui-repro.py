#!/usr/bin/env python3
"""Fork-only UI evidence. Uses synthetic nodes and no remote proxy server."""
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

PKG = "io.github.madeye.meow"
SDK = Path(os.environ["ANDROID_HOME"])
ADB = str(SDK / "platform-tools/adb")
OUT = Path(os.environ.get("OUT", "proxy-editor-evidence")).resolve()
APK = str(Path(os.environ["APK"]).resolve())
OUT.mkdir(parents=True, exist_ok=True)
PROCESSES = []
CHECKS = []


def run(args, timeout=20, check=True, binary=False):
    return subprocess.run(args, capture_output=True, timeout=timeout, check=check,
                          text=not binary).stdout


def adb(*args, **kwargs):
    return run([ADB, *args], **kwargs)


def info(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)


def check(condition, description):
    CHECKS.append({"check": description, "passed": bool(condition)})
    if not condition:
        raise AssertionError(description)
    info("PASS " + description)


def dump():
    for _ in range(4):
        adb("shell", "rm", "-f", "/sdcard/proxy-ui.xml", check=False)
        adb("shell", "uiautomator", "dump", "/sdcard/proxy-ui.xml", timeout=15, check=False)
        xml = adb("shell", "cat", "/sdcard/proxy-ui.xml", check=False)
        try:
            return ET.fromstring(xml)
        except ET.ParseError:
            time.sleep(0.5)
    raise RuntimeError("uiautomator did not produce fresh XML")


def nodes(root):
    return list(root.iter("node"))


def by_id(resource):
    return lambda n: n.get("resource-id") == resource


def by_text(text):
    return lambda n: n.get("text") == text


def find(root, predicate):
    return next((n for n in nodes(root) if predicate(n)), None)


def texts(node):
    return [n.get("text", "") for n in node.iter("node") if n.get("text")]


def wait_for(predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root = dump()
        node = find(root, predicate)
        if node is not None:
            return node
        time.sleep(0.5)
    raise RuntimeError("UI node not found within deadline")


def tap(node):
    bounds = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
    if len(bounds) != 4:
        raise RuntimeError("Invalid UI bounds")
    x1, y1, x2, y2 = bounds
    check(x2 > x1 and y2 > y1, "tap target has visible bounds")
    adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
    time.sleep(0.35)


def tap_match(predicate):
    tap(wait_for(predicate))


def scroll_to(predicate, down=True, attempts=9):
    for _ in range(attempts):
        root = dump()
        node = find(root, predicate)
        if node is not None:
            return node
        y1, y2 = (1060, 380) if down else (380, 1060)
        adb("shell", "input", "swipe", "540", str(y1), "540", str(y2), "350")
        time.sleep(0.25)
    raise RuntimeError("Scrollable UI field was not found")


def shot(name):
    time.sleep(0.3)
    data = adb("exec-out", "screencap", "-p", binary=True)
    check(data.startswith(b"\x89PNG\r\n\x1a\n"), "screenshot PNG is valid: " + name)
    (OUT / (name + ".png")).write_bytes(data)
    root = dump()
    ET.ElementTree(root).write(OUT / (name + ".xml"), encoding="utf-8")
    info("Screenshot " + name)
    return root


def process(command, output):
    stream = open(OUT / output, "wb")
    child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                             start_new_session=True)
    PROCESSES.append((child, stream))
    return child


def seed_profile():
    yaml = """mixed-port: 7890
mode: rule
log-level: info
ipv6: false
allow-lan: false
dns:
  enable: true
  listen: 127.0.0.1:1053
  nameserver: [1.1.1.1]
proxies:
  - {name: AAA Snell Editor, type: snell, server: 127.0.0.1, port: 1443, psk: synthetic-ui-test-key, version: 4, obfs-opts: {mode: http, host: synthetic.example}, dialer-proxy: Relay A}
  - name: Relay A
    type: socks5
    server: 127.0.0.1
    port: 1080
  - name: Relay B
    type: socks5
    server: 127.0.0.1
    port: 1081
proxy-groups:
  - name: Relay Group
    type: select
    proxies: [Relay A, Relay B, DIRECT]
rules:
  - MATCH,DIRECT
"""
    adb("shell", "am", "start", "-W", "-n", PKG + "/.MainActivity", timeout=45)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if "meow.db" in adb("shell", "run-as", PKG, "ls", "databases", check=False):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("Application Room database was not initialized")
    adb("shell", "am", "force-stop", PKG)
    with tempfile.TemporaryDirectory() as scratch:
        dbfile = Path(scratch) / "meow.db"
        db = sqlite3.connect(dbfile)
        schema = json.loads(Path("core/schemas/io.github.madeye.meow.database.PrivateDatabase/4.json").read_text())["database"]
        db.execute("PRAGMA user_version = " + str(schema["version"]))
        for entity in schema["entities"]:
            db.execute(entity["createSql"].replace("${TABLE_NAME}", entity["tableName"]))
        db.execute("CREATE TABLE room_master_table (id INTEGER PRIMARY KEY, identity_hash TEXT)")
        db.execute("INSERT INTO room_master_table VALUES (42, ?)", (schema["identityHash"],))
        db.execute("INSERT INTO clash_profile (name,url,yaml_content,selected,last_updated,tx,rx,selected_proxy,yaml_backup) VALUES (?,?,?,1,?,0,0,'','')",
                   ("Synthetic Editor UI", "", yaml, int(time.time()*1000)))
        db.commit()
        db.close()
        adb("push", str(dbfile), "/data/local/tmp/editor-ui.db")
        adb("shell", "cat /data/local/tmp/editor-ui.db | run-as " + PKG + " sh -c 'cat > databases/meow.db'")
        adb("shell", "run-as", PKG, "rm", "-f", "databases/meow.db-wal", "databases/meow.db-shm")
        adb("shell", "rm", "-f", "/data/local/tmp/editor-ui.db")


def main():
    adb("start-server")
    emulator = process([str(SDK / "emulator/emulator"), "-avd", "proxy-edit-ui",
                        "-no-window", "-gpu", "swiftshader_indirect", "-noaudio",
                        "-no-boot-anim", "-no-snapshot", "-accel", "on",
                        "-memory", "2048", "-cores", "2", "-feature", "-Vulkan"],
                       "emulator.log")
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if emulator.poll() is not None:
            raise RuntimeError("Emulator exited before boot")
        if adb("shell", "getprop", "sys.boot_completed", timeout=5, check=False).strip() == "1":
            break
        time.sleep(1)
    else:
        raise RuntimeError("Emulator boot exceeded 150 seconds")
    info("Emulator booted with KVM")
    adb("shell", "wm", "size", "1080x1920")
    adb("shell", "wm", "density", "360")
    adb("shell", "input", "keyevent", "82")
    for key in ["window_animation_scale", "transition_animation_scale", "animator_duration_scale"]:
        adb("shell", "settings", "put", "global", key, "0")
    adb("install", "-r", "-g", APK, timeout=60)
    adb("logcat", "-c")
    process([ADB, "logcat", "-v", "threadtime"], "logcat.log")
    seed_profile()
    adb("shell", "am", "start", "-W", "-n", PKG + "/.MainActivity", "--ez", "auto_connect", "true", timeout=45)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if "inet " in adb("shell", "ip", "addr", "show", "tun0", check=False):
            break
        root = dump()
        if find(root, lambda n: n.get("package") == "com.android.vpndialogs") is not None:
            node = find(root, by_id("android:id/button1"))
            if node is not None:
                tap(node)
        time.sleep(0.5)
    else:
        raise RuntimeError("Synthetic profile VPN did not start")
    check(True, "Synthetic profile starts the actual VPN engine")
    tap_match(by_text("Proxy Servers"))
    tap_match(by_id("proxy_servers_edit_AAA Snell Editor"))
    wait_for(by_id("proxy_edit_name"))
    shot("01-snell-editor")

    dialer = scroll_to(by_id("proxy_edit_dialer-proxy"))
    check("Relay A" in texts(dialer), "Dialer opens with its existing Relay A value")
    shot("02-dialer-initial-relay-a")
    tap(dialer)
    root = shot("03-dialer-dropdown")
    check(find(root, by_text("Remove")) is None, "Dialer dropdown contains no Remove menu item")
    check(find(root, lambda n: n.get("content-desc") == "Remove Dialer proxy") is None,
          "Dialer selector contains no destructive remove control")
    for label in ["Relay A", "Relay B", "Relay Group"]:
        check(find(root, by_text(label)) is not None, "Dialer dropdown offers " + label)
    tap_match(by_text("Relay Group"))
    check("Relay Group" in texts(wait_for(by_id("proxy_edit_dialer-proxy"))),
          "Selecting Relay Group updates the visible dialer value")
    shot("04-dialer-selected-relay-group")

    # The flow fixture starts with HTTP obfuscation, which Snell v6 correctly
    # rejects. Turn it off through the actual form before changing versions.
    obfs = scroll_to(by_id("proxy_edit_obfs-opts.mode"), down=False)
    tap(obfs)
    tap_match(by_text("off"))
    check("off" in texts(wait_for(by_id("proxy_edit_obfs-opts.mode"))),
          "Obfuscation can be disabled before switching to Snell v6")
    version = scroll_to(by_id("proxy_edit_version"), down=False)
    check("4" in texts(version), "Snell version initially shows 4")
    tap(version)
    shot("05-snell-version-dropdown")
    tap_match(by_text("6"))
    check("6" in texts(wait_for(by_id("proxy_edit_version"))), "Snell version can change to 6")
    scroll_to(by_id("proxy_edit_mode"))
    shot("06-snell-version-6")
    tap_match(by_id("proxy_edit_save"))
    wait_for(by_id("proxy_servers_root"))
    check(True, "Saving the modified Snell proxy returns to Proxy Servers")

    # Read persisted YAML after stopping the app, including SQLite's WAL.
    # Only synthetic assertions are retained; database bytes stay in /tmp.
    adb("shell", "am", "force-stop", PKG)
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "meow.db"
        path.write_bytes(adb("exec-out", "run-as", PKG, "cat", "databases/meow.db", binary=True))
        wal = adb("exec-out", "run-as", PKG, "cat", "databases/meow.db-wal", binary=True, check=False)
        if wal:
            Path(str(path) + "-wal").write_bytes(wal)
        db = sqlite3.connect(path)
        yaml = db.execute("SELECT yaml_content FROM clash_profile WHERE selected = 1").fetchone()[0]
        db.close()
        check(bool(re.search(r'''dialer-proxy:\s*['"]?Relay Group\b''', yaml)),
              "Room YAML persists dialer-proxy Relay Group")
        check(bool(re.search(r'''version:\s*['"]?6\b''', yaml)),
              "Room YAML persists Snell version 6")
    adb("shell", "am", "start", "-W", "-n", PKG + "/.MainActivity", "-f", "0x10008000", "--ez", "auto_connect", "true", timeout=45)
    tap_match(by_text("Proxy Servers"))
    tap_match(by_id("proxy_servers_edit_AAA Snell Editor"))
    wait_for(by_id("proxy_edit_name"))
    version = scroll_to(by_id("proxy_edit_version"))
    check("6" in texts(version), "Reopened Snell editor preserves version 6")
    shot("07-reopened-snell-version-6")
    dialer = scroll_to(by_id("proxy_edit_dialer-proxy"))
    check("Relay Group" in texts(dialer), "Reopened editor preserves the selected Relay Group dialer")
    shot("08-reopened-dialer-relay-group")
    info("All required editor UI checks passed")


def interrupted(signum, frame):
    raise RuntimeError("Harness interrupted by signal " + str(signum))


signal.signal(signal.SIGTERM, interrupted)
try:
    main()
except BaseException:
    try:
        shot("failure")
    except BaseException as error:
        info("Failure screenshot unavailable: " + str(error))
    raise
finally:
    (OUT / "checks.json").write_text(json.dumps(CHECKS, indent=2) + "\n")
    for child, stream in reversed(PROCESSES):
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        stream.close()
    info("Background processes cleaned up without adb emu kill")
