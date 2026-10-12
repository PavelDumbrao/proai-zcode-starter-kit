#!/usr/bin/env python3
"""PROAI ZCode Starter Kit: additive, offline, idempotent installation.

Only reads/writes ~/ .zcode (or PROAI_ZCODE_HOME for isolated testing).
Never transmits, prints, or copies existing configuration values outside local backup.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SKILL_NAMES = ('proai-first-project', 'proai-api-basics', 'proai-debug-and-test', 'proai-ui-quality')
RULES_START = '<!-- PROAI_STARTER_RULES_BEGIN:v2 -->'
RULES_END = '<!-- PROAI_STARTER_RULES_END:v2 -->'
OLD_RULES_START = '<!-- PROAI_STARTER_RULES_BEGIN:v1 -->'
OLD_RULES_END = '<!-- PROAI_STARTER_RULES_END:v1 -->'
OLD_RULES_SHA256 = 'e5ce330f36d020467797483a183cf774064bc4ea23bdb9a113b86a3cbd273fed'
HOOKS = {
    'PreToolUse': ('proai_pre_tool_use.py', 'Bash|execute_command|shell', 5),
    'PostToolUse': ('proai_post_tool_use.py', 'Write|Edit|MultiEdit', 5),
}


class InstallationError(Exception):
    pass


def install_home():
    return Path(os.environ.get('PROAI_ZCODE_HOME', str(Path.home() / '.zcode'))).expanduser()


def validate_target(p):
    # Fail closed on symlinks INSIDE the target ZCode directory.  On macOS
    # /tmp is commonly a symlink to /private/tmp; that OS-level link must not
    # make tests falsely fail.  Never inspect or modify paths above target root.
    root = install_home()
    try:
        p.relative_to(root)
    except ValueError:
        raise InstallationError('Цель вне выбранного каталога ZCode: ' + str(p))
    current = p
    while current != root.parent:
        if current.is_symlink():
            raise InstallationError('Цель содержит символьную ссылку: ' + str(current))
        current = current.parent


def backup(path):
    if path.exists():
        stem = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        target = path.with_name(path.name + '.proai-backup-' + stem)
        shutil.copy2(path, target)
        print('BACKUP', target)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.proai-', dir=str(path.parent))
    tmp = Path(temp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        else:
            os.chmod(tmp, 0o600 if path.suffix == '.json' else 0o644)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def hook_command(home, filename):
    args = [sys.executable, str(home / 'hooks' / filename)]
    if os.name == 'nt':
        return subprocess.list2cmdline(args)
    return ' '.join(shlex.quote(x) for x in args)


def desired_rules(home, text):
    block = (ROOT / 'rules.md').read_text(encoding='utf-8').strip()
    if block.count(RULES_START) != 1 or block.count(RULES_END) != 1:
        raise InstallationError('Учебные правила не содержат корректные метки v2')
    if text is None:
        return block + '\n'
    # When an already-installed v2 block has been edited, never overwrite it:
    # the owner may have personalized that portion of their instructions.
    if RULES_START in text or RULES_END in text:
        if text.count(RULES_START) != 1 or text.count(RULES_END) != 1:
            raise InstallationError('Неполный/повторный PROAI v2 блок в AGENTS.md')
        start = text.index(RULES_START)
        end = text.index(RULES_END) + len(RULES_END)
        if end <= start:
            raise InstallationError('Нарушен порядок меток PROAI v2')
        if text[start:end] != block[block.index(RULES_START):block.index(RULES_END)+len(RULES_END)]:
            raise InstallationError('PROAI v2 правила изменены пользователем: не перезаписываю')
        return text
    # v1->v1.1 migration is allowed ONLY for the exact stock v1 rules.
    if OLD_RULES_START in text or OLD_RULES_END in text:
        if text.count(OLD_RULES_START) != 1 or text.count(OLD_RULES_END) != 1:
            raise InstallationError('Неполный/повторный старый PROAI блок в AGENTS.md')
        start = text.index(OLD_RULES_START)
        end = text.index(OLD_RULES_END) + len(OLD_RULES_END)
        old = text[start:end]
        if hashlib.sha256(old.encode('utf-8')).hexdigest() != OLD_RULES_SHA256:
            raise InstallationError('Старые PROAI правила изменены пользователем: не перезаписываю')
        return text[:start] + block + text[end:]
    return text.rstrip() + '\n\n' + block + '\n'


def amended_config(home, original):
    if not isinstance(original, dict):
        raise InstallationError('Некорректная структура ZCode config.json')
    c = json.loads(json.dumps(original, ensure_ascii=False))
    hc = c.setdefault('hooks', {})
    if not isinstance(hc, dict):
        raise InstallationError('Некорректная структура hooks')
    if 'enabled' not in hc:
        hc['enabled'] = True
    if 'maxOutputBytes' not in hc:
        hc['maxOutputBytes'] = 4096
    ev = hc.setdefault('events', {})
    if not isinstance(ev, dict):
        raise InstallationError('hooks.events должен быть объектом')
    for event, (filename, matcher, timeout) in HOOKS.items():
        entries = ev.setdefault(event, [])
        if not isinstance(entries, list):
            raise InstallationError('Неожиданный список Hooks для события: ' + event)
        cmd = hook_command(home, filename)
        already = any(isinstance(entry, dict) and
                      any(isinstance(x, dict) and filename in str(x.get('command', ''))
                          for x in entry.get('hooks', []) if isinstance(entry.get('hooks'), list))
                      for entry in entries)
        if not already:
            entries.append({'matcher': matcher, 'hooks': [{'type': 'command', 'command': cmd, 'timeout': timeout}]})
    return c


def plan(home, with_hooks):
    if not ROOT.joinpath('rules.md').is_file():
        raise InstallationError('Рядом с установщиком не найден rules.md')
    ops = []
    rpath = home / 'AGENTS.md'
    validate_target(rpath)
    old = rpath.read_text(encoding='utf-8') if rpath.is_file() else None
    nxt = desired_rules(home, old)
    if old != nxt:
        ops.append(('rules', rpath, nxt))
    for name in SKILL_NAMES:
        src = ROOT / 'skills' / name / 'SKILL.md'
        dst = home / 'skills' / name / 'SKILL.md'
        validate_target(dst)
        if not src.is_file():
            raise InstallationError('Нет учебного Skill: ' + name)
        text = src.read_text(encoding='utf-8')
        if dst.exists() and dst.read_text(encoding='utf-8') != text:
            raise InstallationError('Другое содержимое существующего Skill: ' + str(dst))
        if not dst.exists():
            ops.append(('skill', dst, text))
    if with_hooks:
        for event, (filename, _, _) in HOOKS.items():
            src = ROOT / 'hook-scripts' / filename
            dst = home / 'hooks' / filename
            validate_target(dst)
            if not src.is_file():
                raise InstallationError('Нет учебного Hook: ' + filename)
            text = src.read_text(encoding='utf-8')
            if dst.exists() and dst.read_text(encoding='utf-8') != text:
                raise InstallationError('Другое содержимое существующего Hook: ' + str(dst))
            if not dst.exists():
                ops.append(('hook', dst, text))
        conf = home / 'cli' / 'config.json'
        validate_target(conf)
        orig_text = conf.read_text(encoding='utf-8') if conf.exists() else None
        try:
            orig = json.loads(orig_text) if orig_text is not None else {}
        except json.JSONDecodeError as e:
            raise InstallationError('config.json не является валидным JSON: ' + str(e))
        new = amended_config(home, orig)
        if new != orig:
            ops.append(('config', conf, json.dumps(new, ensure_ascii=False, indent=2) + '\n'))
        if new.get('hooks', {}).get('enabled') is False:
            print('WARNING: существующий hooks.enabled=false. Не включаю Hooks против выбора пользователя.')
    return ops


def verify(home, with_hooks):
    errors = []
    f = home / 'AGENTS.md'
    if not f.is_file() or RULES_START not in f.read_text(encoding='utf-8'):
        errors.append('Глобальные инструкции отсутствуют')
    for name in SKILL_NAMES:
        p = home / 'skills' / name / 'SKILL.md'
        s = ROOT / 'skills' / name / 'SKILL.md'
        if not p.is_file() or p.read_bytes() != s.read_bytes():
            errors.append('Skill отсутствует/отличается: ' + name)
    if with_hooks:
        cf = home / 'cli' / 'config.json'
        if not cf.is_file():
            errors.append('Hook config.json отсутствует')
        else:
            try:
                obj = json.loads(cf.read_text(encoding='utf-8'))
                events = obj.get('hooks', {}).get('events', {})
                for event, (filename, _, _) in HOOKS.items():
                    file = home / 'hooks' / filename
                    if not file.is_file() or file.read_bytes() != (ROOT / 'hook-scripts' / filename).read_bytes():
                        errors.append('Hook отсутствует/отличается: ' + filename)
                    entries = events.get(event, [])
                    if not any(isinstance(x, dict) and any(isinstance(y, dict) and filename in str(y.get('command', ''))
                        for y in x.get('hooks', [])) for x in entries):
                        errors.append('Hook не прописан в конфигурации: ' + filename)
                if obj.get('hooks', {}).get('enabled') is False:
                    errors.append('Hooks выключены в существующем конфиге')
            except (OSError, ValueError, AttributeError, TypeError) as exc:
                errors.append('Ошибка чтения config.json: ' + type(exc).__name__)
    if errors:
        for x in errors:
            print('NOT_VERIFIED:', x)
        return 1
    print('VERIFIED: правила,', len(SKILL_NAMES), 'Skills,' , (str(len(HOOKS)) + ' Hooks' if with_hooks else 'Hooks не проверялись'))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dry-run', action='store_true', help='Только посмотреть план; никаких изменений')
    p.add_argument('--with-hooks', action='store_true', help='Дополнительно включить безопасные Hooks')
    p.add_argument('--verify', action='store_true', help='Проверить результаты установки')
    a = p.parse_args()
    home = install_home()
    if a.verify:
        return verify(home, a.with_hooks)
    try:
        ops = plan(home, a.with_hooks)  # Preflight all files before first write.
        if not ops:
            print('ALREADY_INSTALLED: обновлять ничего не нужно')
            return verify(home, a.with_hooks)
        for kind, path, content in ops:
            print(('DRY_RUN' if a.dry_run else 'INSTALL'), kind, str(path))
        if a.dry_run:
            print('DRY_RUN_OK: никаких изменений не внесено')
            return 0
        for kind, path, content in ops:
            if kind in ('rules', 'config') and path.exists():
                backup(path)
            atomic_write(path, content)
        return verify(home, a.with_hooks)
    except (InstallationError, OSError, UnicodeError) as exc:
        print('INSTALL_STOP:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
