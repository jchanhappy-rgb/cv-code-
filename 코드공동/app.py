"""
MineScript Hub - Minecraft Bedrock Script API 공동 개발 플랫폼
단일 파일 풀스택 웹앱 (Flask + SocketIO)
"""
from flask import Flask, request, jsonify, session, redirect, url_for, Response
from flask_socketio import SocketIO, emit, join_room, leave_room
import os, json, uuid, time, hashlib, zipfile, io, base64
from datetime import datetime
from collections import defaultdict
import requests
import re

def load_env_file():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f'.env 로드 실패: {e}')

load_env_file()

# OpenRouter (OpenAI 호환) — CoBuddy: baidu/cobuddy:free
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_API_URL = os.environ.get('OPENROUTER_API_URL', 'https://openrouter.ai/api/v1/chat/completions')
OPENROUTER_APP_NAME = os.environ.get('OPENROUTER_APP_NAME', 'MineScript Hub')

PROVIDER_MODELS = {
    'openai': {
        'gpt-5.5-pro': 'openai/gpt-4o',
        'gpt-4o': 'openai/gpt-4o',
        'gpt-4-turbo': 'openai/gpt-4-turbo',
    },
    'cobuddy': {
        'cobuddy': 'baidu/cobuddy:free',
        'cobuddy-free': 'baidu/cobuddy:free',
        'gpt-5.5-pro': 'baidu/cobuddy:free',
    },
}

app = Flask(__name__)
app.config['SECRET_KEY'] = 'minescript-hub-secret-key-change-in-production'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', max_http_buffer_size=50*1024*1024)
DATA_FILE = os.path.join(os.path.dirname(__file__), 'minescript_data.json')

# ==================== DB ====================
DB = {
    'users': {},
    'projects': {},
    'sessions': {},
    'online_users': {},   # project_id -> {username: {file, line, col, selection}}
    'active_users': defaultdict(set), # username -> socket session ids
    'socket_users': {},    # socket session id -> username
    'socket_projects': {}, # socket session id -> project id
    'bridge_status': {},   # project_id -> status dict
    'editing_status': {}, # project_id -> {file: {user: timestamp}}
    'user_settings': {},  # username -> settings dict
}

def persistent_snapshot():
    return {
        'users': DB['users'],
        'projects': DB['projects'],
        'user_settings': DB['user_settings'],
        'bridge_status': DB.get('bridge_status', {}),
    }

def save_db():
    tmp = DATA_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(persistent_snapshot(), f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)

def load_db():
    if not os.path.exists(DATA_FILE):
        return False
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        DB['users'] = data.get('users', {})
        DB['projects'] = data.get('projects', {})
        DB['user_settings'] = data.get('user_settings', {})
        DB['bridge_status'] = data.get('bridge_status', {})
        return bool(DB['users'] or DB['projects'])
    except Exception as e:
        print(f'데이터 로드 실패: {e}')
        return False

def init_demo_data():
    DB['users']['demo'] = {
        'password': hashlib.sha256('demo'.encode()).hexdigest(),
        'email': 'demo@minescript.hub',
        'avatar': '🧑‍💻',
        'bio': 'Bedrock Script API 개발자',
        'followers': ['steve', 'alex'],
        'following': ['steve'],
        'created_at': time.time(),
    }
    DB['users']['steve'] = {
        'password': hashlib.sha256('steve'.encode()).hexdigest(),
        'email': 'steve@mc.com', 'avatar': '⛏️', 'bio': 'Minecraft 마니아',
        'followers': ['demo'], 'following': ['demo', 'alex'], 'created_at': time.time(),
    }
    DB['users']['alex'] = {
        'password': hashlib.sha256('alex'.encode()).hexdigest(),
        'email': 'alex@mc.com', 'avatar': '🗡️', 'bio': 'Addon 크리에이터',
        'followers': ['steve'], 'following': ['demo'], 'created_at': time.time(),
    }

    pid = 'demo-project-1'
    DB['projects'][pid] = {
        'id': pid,
        'name': '폭발 검 애드온',
        'owner': 'demo',
        'description': '오른클릭 시 폭발하는 마법의 검을 추가하는 Behavior Pack',
        'thumbnail': '💥',
        'tags': ['weapon', 'magic', 'behavior-pack'],
        'public': True,
        'likes': ['steve', 'alex'],
        'downloads': 142,
        'members': {'demo': 'admin', 'steve': 'developer', 'alex': 'viewer'},
        'chat': [
            {'user': 'steve', 'msg': '폭발 반경은 얼마로 설정할까요?', 'time': time.time() - 3600},
            {'user': 'demo', 'msg': '기본 3블록으로 시작해보죠 💣', 'time': time.time() - 3500},
        ],
        'files': {
            'manifest.json': json.dumps({
                "format_version": 2,
                "header": {
                    "name": "Explosive Sword",
                    "description": "Adds an explosive sword",
                    "uuid": str(uuid.uuid4()),
                    "version": [1, 0, 0],
                    "min_engine_version": [1, 20, 0]
                },
                "modules": [{
                    "type": "script",
                    "language": "javascript",
                    "uuid": str(uuid.uuid4()),
                    "version": [1, 0, 0],
                    "entry": "scripts/main.js"
                }],
                "dependencies": [
                    {"module_name": "@minecraft/server", "version": "1.10.0"}
                ]
            }, indent=2),
            'scripts/main.js': '''import { world, system } from "@minecraft/server";

// 폭발 검 이벤트 핸들러
world.afterEvents.itemUse.subscribe((event) => {
    const player = event.source;
    const item = event.itemStack;
    
    if (item.typeId === "minescript:explosive_sword") {
        const loc = player.location;
        const dimension = player.dimension;
        
        dimension.createExplosion(loc, 3, {
            breaksBlocks: true,
            causesFire: true,
            source: player
        });
        
        player.sendMessage("§c§l💥 폭발!");
    }
});

console.log("Explosive Sword loaded!");
''',
            'items/explosive_sword.json': json.dumps({
                "format_version": "1.20.0",
                "minecraft:item": {
                    "description": {
                        "identifier": "minescript:explosive_sword",
                        "category": "Equipment"
                    },
                    "components": {
                        "minecraft:max_stack_size": 1,
                        "minecraft:hand_equipped": True,
                        "minecraft:damage": 8,
                        "minecraft:durability": {"max_durability": 500}
                    }
                }
            }, indent=2),
        },
        'created_at': time.time() - 86400 * 3,
    }
    
    DB['projects']['demo-project-2'] = {
        'id': 'demo-project-2', 'name': '커스텀 좀비 엔티티', 'owner': 'alex',
        'description': '강력한 보스 좀비를 추가합니다', 'thumbnail': '🧟',
        'tags': ['entity', 'boss', 'mob'], 'public': True,
        'likes': ['demo'], 'downloads': 87,
        'members': {'alex': 'admin'}, 'chat': [],
        'files': {'manifest.json': '{}', 'scripts/main.js': '// Boss zombie code'},
        'created_at': time.time() - 86400 * 7,
    }
    DB['projects']['demo-project-3'] = {
        'id': 'demo-project-3', 'name': 'RPG 시스템', 'owner': 'steve',
        'description': '레벨, 경험치, 스킬 시스템', 'thumbnail': '⚔️',
        'tags': ['rpg', 'system', 'gameplay'], 'public': True,
        'likes': ['demo', 'alex'], 'downloads': 234,
        'members': {'steve': 'admin'}, 'chat': [],
        'files': {'manifest.json': '{}'},
        'created_at': time.time() - 86400 * 2,
    }

if not load_db():
    init_demo_data()
    save_db()

# ==================== 헬퍼 ====================
def current_user():
    sid = session.get('sid')
    return DB['sessions'].get(sid) if sid else None

def require_login():
    u = current_user()
    return u if u else None

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def default_settings():
    return {
        'theme': 'minescript',
        'fontSize': 14,
        'fontFamily': 'JetBrains Mono',
        'tabSize': 2,
        'minimap': True,
        'wordWrap': False,
        'lineNumbers': True,
        'autoSave': True,
        'autoSaveDelay': 1000,
        'autoComplete': True,
        'formatOnPaste': True,
        'renderWhitespace': 'selection',
        'cursorBlinking': 'smooth',
        'cursorStyle': 'line',
        'showCollaborators': True,
        'showEditingIndicator': True,
    }

def is_online(username):
    return bool(DB['active_users'].get(username))

def public_user(username):
    user = DB['users'].get(username)
    if not user:
        return None
    info = user.copy()
    info.pop('password', None)
    info['username'] = username
    info['online'] = is_online(username)
    return info

def ensure_bridge_token(project):
    if not project.get('bridge_token'):
        project['bridge_token'] = str(uuid.uuid4())
        save_db()
    return project['bridge_token']

def bridge_room(pid):
    return f'bridge:{pid}'

def emit_bridge(pid, event, payload):
    socketio.emit(event, payload, room=bridge_room(pid))

PERMISSION_DEFAULTS = {
    'admin': {
        'edit_code': True, 'manage_files': True, 'invite_members': True,
        'manage_roles': True, 'bedrock_sync': True, 'chat': True,
    },
    'developer': {
        'edit_code': True, 'manage_files': True, 'invite_members': True,
        'manage_roles': False, 'bedrock_sync': True, 'chat': True,
    },
    'viewer': {
        'edit_code': False, 'manage_files': False, 'invite_members': False,
        'manage_roles': False, 'bedrock_sync': False, 'chat': True,
    },
}

def member_role(project, username):
    member = project.get('members', {}).get(username)
    if isinstance(member, dict):
        return member.get('role', 'viewer')
    return member or 'viewer'

def member_permissions(project, username):
    role = member_role(project, username)
    perms = PERMISSION_DEFAULTS.get(role, PERMISSION_DEFAULTS['viewer']).copy()
    custom = project.get('member_permissions', {}).get(username, {})
    perms.update({k: bool(v) for k, v in custom.items() if k in perms})
    if username == project.get('owner'):
        perms.update(PERMISSION_DEFAULTS['admin'])
    return perms

def can_project(project, username, permission):
    return bool(username and member_permissions(project, username).get(permission))

def project_for_client(project):
    data = project.copy()
    data.pop('bridge_token', None)
    data['member_permissions'] = {
        username: member_permissions(project, username)
        for username in project.get('members', {})
    }
    return data

# ==================== API ====================
@app.route('/api/register', methods=['POST'])
def api_register():
    d = request.json
    u, p = d.get('username','').strip(), d.get('password','')
    if not u or not p: return jsonify({'error':'아이디/비밀번호 필요'}), 400
    if u in DB['users']: return jsonify({'error':'이미 존재하는 아이디'}), 400
    DB['users'][u] = {
        'password': hash_pw(p), 'email': d.get('email',''),
        'avatar': d.get('avatar','👤'), 'bio': '', 'followers': [], 'following': [],
        'created_at': time.time(),
    }
    DB['user_settings'][u] = default_settings()
    sid = str(uuid.uuid4())
    DB['sessions'][sid] = u
    session['sid'] = sid
    save_db()
    return jsonify({'ok': True, 'username': u})

@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.json
    u, p = d.get('username','').strip(), d.get('password','')
    user = DB['users'].get(u)
    if not user or user['password'] != hash_pw(p):
        return jsonify({'error':'아이디 또는 비밀번호 오류'}), 401
    sid = str(uuid.uuid4())
    DB['sessions'][sid] = u
    session['sid'] = sid
    if u not in DB['user_settings']:
        DB['user_settings'][u] = default_settings()
        save_db()
    return jsonify({'ok': True, 'username': u})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    sid = session.get('sid')
    if sid: DB['sessions'].pop(sid, None)
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
def api_me():
    u = current_user()
    if not u: return jsonify({'user': None})
    user = DB['users'][u].copy()
    user.pop('password', None)
    user['username'] = u
    user['settings'] = DB['user_settings'].get(u, default_settings())
    return jsonify({'user': user})

@app.route('/api/settings', methods=['POST'])
def api_save_settings():
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    s = DB['user_settings'].get(u, default_settings())
    s.update(request.json or {})
    DB['user_settings'][u] = s
    save_db()
    return jsonify({'ok': True, 'settings': s})

@app.route('/api/profile', methods=['POST'])
def api_save_profile():
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    d = request.json or {}
    user = DB['users'][u]
    if 'avatar' in d: user['avatar'] = d['avatar']
    if 'bio' in d: user['bio'] = d['bio']
    if 'email' in d: user['email'] = d['email']
    save_db()
    return jsonify({'ok': True})

@app.route('/api/projects')
def api_projects():
    q = request.args.get('q','').lower()
    sort = request.args.get('sort','recent')
    result = []
    for pid, p in DB['projects'].items():
        if not p['public'] and p['owner'] != current_user(): continue
        if q and q not in p['name'].lower() and q not in p['description'].lower() and not any(q in t for t in p['tags']): 
            continue
        result.append({
            'id': pid, 'name': p['name'], 'owner': p['owner'],
            'description': p['description'], 'thumbnail': p['thumbnail'],
            'tags': p['tags'], 'likes': len(p['likes']), 'downloads': p['downloads'],
            'created_at': p['created_at'],
        })
    if sort == 'popular': result.sort(key=lambda x: x['likes'], reverse=True)
    elif sort == 'downloads': result.sort(key=lambda x: x['downloads'], reverse=True)
    else: result.sort(key=lambda x: x['created_at'], reverse=True)
    return jsonify({'projects': result})

@app.route('/api/projects/<pid>')
def api_project(pid):
    p = DB['projects'].get(pid)
    if not p: return jsonify({'error':'없음'}), 404
    return jsonify({'project': project_for_client(p)})

@app.route('/api/projects', methods=['POST'])
def api_create_project():
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    d = request.json
    pid = str(uuid.uuid4())[:8]
    template = d.get('template', 'empty')
    
    files = {}
    if template in ('behavior', 'both'):
        files['manifest.json'] = json.dumps({
            "format_version": 2,
            "header": {"name": d.get('name','New Pack'), "description": "", 
                      "uuid": str(uuid.uuid4()), "version":[1,0,0], "min_engine_version":[1,20,0]},
            "modules": [{"type":"script","language":"javascript","uuid":str(uuid.uuid4()),
                        "version":[1,0,0],"entry":"scripts/main.js"}],
            "dependencies": [{"module_name":"@minecraft/server","version":"1.10.0"}]
        }, indent=2)
        files['scripts/main.js'] = 'import { world, system } from "@minecraft/server";\n\nworld.sendMessage("Hello, Minecraft!");\n'
    if template == 'resource':
        files['manifest.json'] = json.dumps({
            "format_version": 2,
            "header": {"name": d.get('name','Resource Pack'), "description":"",
                      "uuid": str(uuid.uuid4()), "version":[1,0,0], "min_engine_version":[1,20,0]},
            "modules":[{"type":"resources","uuid":str(uuid.uuid4()),"version":[1,0,0]}]
        }, indent=2)
    
    DB['projects'][pid] = {
        'id': pid, 'name': d.get('name','New Project'), 'owner': u,
        'description': d.get('description',''), 'thumbnail': d.get('thumbnail','📦'),
        'tags': d.get('tags',[]), 'public': d.get('public', True),
        'likes': [], 'downloads': 0, 'members': {u:'admin'}, 'chat': [],
        'files': files, 'created_at': time.time(),
    }
    save_db()
    return jsonify({'ok': True, 'id': pid})

# 팩 업로드
@app.route('/api/projects/upload', methods=['POST'])
def api_upload_pack():
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    
    f = request.files.get('pack')
    if not f: return jsonify({'error':'파일이 없습니다'}), 400
    
    name = request.form.get('name', f.filename.rsplit('.',1)[0])
    description = request.form.get('description', '')
    public = request.form.get('public', 'true') == 'true'
    
    pid = str(uuid.uuid4())[:8]
    files = {}
    
    try:
        data = f.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir(): continue
                if info.file_size > 5 * 1024 * 1024:  # 5MB 단일 파일 제한
                    continue
                fname = info.filename
                try:
                    content = zf.read(info).decode('utf-8')
                except UnicodeDecodeError:
                    # 바이너리는 base64 (간단 처리 - 텍스트 파일만 우선)
                    raw = zf.read(info)
                    if len(raw) < 100000:
                        content = 'data:base64,' + base64.b64encode(raw).decode('ascii')
                    else:
                        continue
                files[fname] = content
    except zipfile.BadZipFile:
        return jsonify({'error':'유효하지 않은 .zip/.mcpack 파일'}), 400
    except Exception as e:
        return jsonify({'error': f'업로드 오류: {e}'}), 400
    
    if not files:
        return jsonify({'error':'파일을 추출할 수 없습니다'}), 400
    
    DB['projects'][pid] = {
        'id': pid, 'name': name, 'owner': u,
        'description': description, 'thumbnail': '📦',
        'tags': ['uploaded'], 'public': public,
        'likes': [], 'downloads': 0, 'members': {u:'admin'}, 'chat': [],
        'files': files, 'created_at': time.time(),
    }
    save_db()
    return jsonify({'ok': True, 'id': pid, 'files': len(files)})

@app.route('/api/projects/<pid>/files', methods=['POST'])
def api_save_file(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p: return jsonify({'error':'없음'}), 404
    if not can_project(p, u, 'edit_code'):
        return jsonify({'error':'권한 없음'}), 403
    d = request.json
    path, content = d.get('path'), d.get('content','')
    p['files'][path] = content
    save_db()
    socketio.emit('file_updated', {'path':path, 'content':content, 'user':u}, room=pid)
    emit_bridge(pid, 'bridge_file_updated', {'path': path, 'content': content, 'user': u, 'time': time.time()})
    return jsonify({'ok': True})

@app.route('/api/projects/<pid>/files/rename', methods=['POST'])
def api_rename_file(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p or not can_project(p, u, 'manage_files'):
        return jsonify({'error':'권한'}), 403
    d = request.json
    old, new = d.get('old'), d.get('new')
    if not old or not new or old not in p['files']:
        return jsonify({'error':'파일 없음'}), 404
    if new in p['files']:
        return jsonify({'error':'이미 존재하는 이름'}), 400
    p['files'][new] = p['files'].pop(old)
    save_db()
    socketio.emit('file_renamed', {'old':old, 'new':new, 'user':u}, room=pid)
    emit_bridge(pid, 'bridge_file_renamed', {'old': old, 'new': new, 'user': u, 'time': time.time()})
    return jsonify({'ok': True})

@app.route('/api/projects/<pid>/files', methods=['DELETE'])
def api_delete_file(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p or not can_project(p, u, 'manage_files'):
        return jsonify({'error':'권한'}), 403
    path = request.json.get('path')
    p['files'].pop(path, None)
    save_db()
    socketio.emit('file_deleted', {'path':path, 'user':u}, room=pid)
    emit_bridge(pid, 'bridge_file_deleted', {'path': path, 'user': u, 'time': time.time()})
    return jsonify({'ok': True})

@app.route('/api/projects/<pid>/like', methods=['POST'])
def api_like(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p: return jsonify({'error':'없음'}), 404
    if u in p['likes']: p['likes'].remove(u)
    else: p['likes'].append(u)
    save_db()
    return jsonify({'likes': len(p['likes']), 'liked': u in p['likes']})

@app.route('/api/projects/<pid>/export')
def api_export(pid):
    p = DB['projects'].get(pid)
    if not p: return 'Not found', 404
    p['downloads'] += 1
    save_db()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, content in p['files'].items():
            if isinstance(content, str) and content.startswith('data:base64,'):
                zf.writestr(path, base64.b64decode(content[12:]))
            else:
                zf.writestr(path, content)
    buf.seek(0)
    return Response(buf.read(), mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{p["name"]}.mcpack"'})

@app.route('/api/projects/<pid>/bridge')
def api_bridge_info(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p or not can_project(p, u, 'bedrock_sync'):
        return jsonify({'error':'권한 없음'}), 403
    token = ensure_bridge_token(p)
    return jsonify({
        'ok': True,
        'pid': pid,
        'token': token,
        'server': request.host_url.rstrip('/'),
        'files': len(p['files']),
    })

@app.route('/api/bridge/<pid>/<token>/files')
def api_bridge_files(pid, token):
    p = DB['projects'].get(pid)
    if not p or ensure_bridge_token(p) != token:
        return jsonify({'error':'bridge 인증 실패'}), 403
    return jsonify({
        'ok': True,
        'project': {'id': pid, 'name': p['name']},
        'files': p['files'],
        'time': time.time(),
    })

@app.route('/api/projects/<pid>/bridge/status')
def api_bridge_status(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p or u not in p['members']:
        return jsonify({'error':'권한 없음'}), 403
    status = DB['bridge_status'].get(pid, {})
    online = bool(status.get('time') and time.time() - status['time'] < 5)
    return jsonify({'ok': True, 'online': online, **status})

@app.route('/api/bridge/<pid>/<token>/status', methods=['POST'])
def api_bridge_ping(pid, token):
    p = DB['projects'].get(pid)
    if not p or ensure_bridge_token(p) != token:
        return jsonify({'error':'bridge 인증 실패'}), 403
    d = request.json or {}
    DB['bridge_status'][pid] = {
        'time': time.time(),
        'files': d.get('files', 0),
        'message': d.get('message', ''),
        'pack_dir': d.get('pack_dir', ''),
    }
    save_db()
    socketio.emit('bridge_status', DB['bridge_status'][pid], room=pid)
    return jsonify({'ok': True})

@app.route('/api/projects/<pid>/bridge/agent.py')
def api_bridge_agent(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p or not can_project(p, u, 'bedrock_sync'):
        return jsonify({'error':'권한 없음'}), 403
    token = ensure_bridge_token(p)
    server = request.host_url.rstrip('/')
    script = f'''# MineScript Hub Bedrock Live Bridge
# 실행 전: pip install "python-socketio[client]" requests
import argparse, base64, os, pathlib, re, sys, time
import requests, socketio

SERVER = {server!r}
PROJECT_ID = {pid!r}
TOKEN = {token!r}

SAFE_SEGMENT = re.compile(r"^[^<>:\\\\|?*]+$")

def safe_path(root, rel):
    parts = [p for p in rel.replace("\\\\", "/").split("/") if p and p not in (".", "..")]
    if not parts or any(not SAFE_SEGMENT.match(p) for p in parts):
        raise ValueError(f"unsafe path: {{rel}}")
    path = pathlib.Path(root).joinpath(*parts).resolve()
    root_path = pathlib.Path(root).resolve()
    if root_path != path and root_path not in path.parents:
        raise ValueError(f"path escapes root: {{rel}}")
    return path

def write_file(root, rel, content):
    path = safe_path(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str) and content.startswith("data:base64,"):
        path.write_bytes(base64.b64decode(content[12:]))
    else:
        path.write_text(content or "", encoding="utf-8")
    print(f"[sync] wrote {{rel}}", flush=True)

def delete_file(root, rel):
    path = safe_path(root, rel)
    if path.exists():
        path.unlink()
        print(f"[sync] deleted {{rel}}", flush=True)

def full_sync(root):
    r = requests.get(f"{{SERVER}}/api/bridge/{{PROJECT_ID}}/{{TOKEN}}/files", timeout=10)
    r.raise_for_status()
    data = r.json()
    for rel, content in data["files"].items():
        write_file(root, rel, content)
    print(f"[sync] full sync complete: {{len(data['files'])}} files", flush=True)

def main():
    parser = argparse.ArgumentParser(description="MineScript Hub Bedrock live bridge")
    parser.add_argument("pack_dir", help="Bedrock development_behavior_packs 또는 development_resource_packs 안의 대상 팩 폴더")
    parser.add_argument("--poll", type=float, default=0, help="Socket.IO 연결 실패 시 폴링 간격(초). 0이면 폴링 안 함")
    args = parser.parse_args()
    root = pathlib.Path(args.pack_dir)
    root.mkdir(parents=True, exist_ok=True)
    full_sync(root)

    sio = socketio.Client(reconnection=True)

    @sio.event
    def connect():
        print("[bridge] connected", flush=True)
        sio.emit("bridge_join", {{"pid": PROJECT_ID, "token": TOKEN}})

    @sio.on("bridge_ready")
    def ready(data):
        print(f"[bridge] ready: {{data.get('project')}}", flush=True)

    @sio.on("bridge_file_updated")
    def file_updated(data):
        write_file(root, data["path"], data.get("content", ""))

    @sio.on("bridge_file_renamed")
    def file_renamed(data):
        old = safe_path(root, data["old"])
        new = safe_path(root, data["new"])
        new.parent.mkdir(parents=True, exist_ok=True)
        if old.exists():
            old.replace(new)
        print(f"[sync] renamed {{data['old']}} -> {{data['new']}}", flush=True)

    @sio.on("bridge_file_deleted")
    def file_deleted(data):
        delete_file(root, data["path"])

    try:
        sio.connect(SERVER, transports=["websocket", "polling"])
        sio.wait()
    except Exception as e:
        print(f"[bridge] socket failed: {{e}}", file=sys.stderr, flush=True)
        if args.poll <= 0:
            raise
        while True:
            full_sync(root)
            time.sleep(args.poll)

if __name__ == "__main__":
    main()
'''
    return Response(script, mimetype='text/x-python',
        headers={'Content-Disposition': f'attachment; filename="minescript_bridge_{pid}.py"'})

@app.route('/api/projects/<pid>/bridge/kit.zip')
def api_bridge_kit(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p or not can_project(p, u, 'bedrock_sync'):
        return jsonify({'error':'권한 없음'}), 403
    token = ensure_bridge_token(p)
    server = request.host_url.rstrip('/')
    safe_name = ''.join(c if c not in '<>:"/\\|?*' else '_' for c in p['name']).strip() or pid
    pack_type = request.args.get('pack_type', 'behavior')
    pack_root = 'development_resource_packs' if pack_type == 'resource' else 'development_behavior_packs'
    default_pack_dir = f'%LOCALAPPDATA%\\Packages\\Microsoft.MinecraftUWP_8wekyb3d8bbwe\\LocalState\\games\\com.mojang\\{pack_root}\\{safe_name}'
    pack_dir = (request.args.get('pack_dir') or default_pack_dir).strip()
    try:
        interval_seconds = max(1, min(10, int(request.args.get('interval', '1'))))
    except ValueError:
        interval_seconds = 1
    delete_removed = request.args.get('delete_removed', 'true') == 'true'
    config = {
        'server': server,
        'project_id': pid,
        'token': token,
        'pack_dir': pack_dir,
        'pack_type': pack_type,
        'interval_seconds': interval_seconds,
        'delete_removed': delete_removed,
    }
    start_bat = '''@echo off
chcp 65001 >nul
title MineScript Hub - Bedrock Live Sync
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync.ps1"
pause
'''
    readme = f'''MineScript Hub Bedrock Live Sync

1. 이 ZIP 파일을 원하는 곳에 압축 해제합니다.
2. start.bat 을 더블클릭합니다.
3. 처음 실행하면 Minecraft Bedrock 개발팩 폴더에 파일이 자동 생성됩니다.
4. 웹사이트에서 Script API 코드나 JSON을 수정하면 약 1초 안에 파일이 반영됩니다.
5. Minecraft 월드에서는 /reload 를 실행하거나 월드를 다시 열면 가장 안정적으로 적용됩니다.

기본 저장 위치:
{default_pack_dir}

다른 폴더에 동기화하고 싶으면 config.json 의 pack_dir 값만 바꾸면 됩니다.
'''
    sync_ps1 = r'''$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ConfigPath = Join-Path $PSScriptRoot "config.json"
if (!(Test-Path $ConfigPath)) {
  Write-Host "config.json not found." -ForegroundColor Red
  exit 1
}

$Config = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
$PackDir = [Environment]::ExpandEnvironmentVariables($Config.pack_dir)
$StatePath = Join-Path $PSScriptRoot ".minescript-synced-files.json"
$Interval = [int]$Config.interval_seconds
if ($Interval -lt 1) { $Interval = 1 }
$DeleteRemoved = $true
if ($null -ne $Config.delete_removed) { $DeleteRemoved = [bool]$Config.delete_removed }

function Get-SafePath($Root, $RelativePath) {
  $parts = $RelativePath -replace "\\", "/" -split "/" | Where-Object { $_ -and $_ -ne "." -and $_ -ne ".." }
  if ($parts.Count -eq 0) { throw "잘못된 파일 경로: $RelativePath" }
  foreach ($part in $parts) {
    if ($part.IndexOfAny([char[]]'<>:"\|?*') -ge 0) { throw "Invalid Windows file name: $RelativePath" }
  }
  $path = [System.IO.Path]::GetFullPath((Join-Path $Root ([System.IO.Path]::Combine($parts))))
  $rootFull = [System.IO.Path]::GetFullPath($Root)
  if (!$path.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Path escapes pack folder: $RelativePath"
  }
  return $path
}

function Load-PreviousFiles {
  if (Test-Path $StatePath) {
    try { return @(Get-Content -Raw -Encoding UTF8 $StatePath | ConvertFrom-Json) } catch { return @() }
  }
  return @()
}

function Save-PreviousFiles($Files) {
  @($Files) | ConvertTo-Json | Set-Content -Encoding UTF8 $StatePath
}

function Sync-Once {
  $url = "$($Config.server)/api/bridge/$($Config.project_id)/$($Config.token)/files"
  $data = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 15
  if (!$data.ok) { throw "서버 응답 오류" }

  New-Item -ItemType Directory -Force -Path $PackDir | Out-Null
  $current = @()
  $files = $data.files.PSObject.Properties

  foreach ($file in $files) {
    $rel = $file.Name
    $current += $rel
    $target = Get-SafePath $PackDir $rel
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $content = [string]$file.Value
    if ($content.StartsWith("data:base64,")) {
      [System.IO.File]::WriteAllBytes($target, [Convert]::FromBase64String($content.Substring(12)))
    } else {
      [System.IO.File]::WriteAllText($target, $content, [System.Text.Encoding]::UTF8)
    }
  }

  if ($DeleteRemoved) {
    $previous = Load-PreviousFiles
    foreach ($old in $previous) {
      if ($current -notcontains $old) {
        try {
          $oldPath = Get-SafePath $PackDir $old
          if (Test-Path $oldPath) { Remove-Item -LiteralPath $oldPath -Force }
        } catch {}
      }
    }
  }
  Save-PreviousFiles $current
  return $current.Count
}

function Send-Status($Count, $Message) {
  try {
    $statusUrl = "$($Config.server)/api/bridge/$($Config.project_id)/$($Config.token)/status"
    $body = @{
      files = $Count
      message = $Message
      pack_dir = $PackDir
    } | ConvertTo-Json
    Invoke-RestMethod -Uri $statusUrl -Method Post -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 5 | Out-Null
  } catch {}
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host " MineScript Hub Bedrock Live Sync" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "Sync folder: $PackDir"
Write-Host "Keep this window open. Press Ctrl + C to stop."
Write-Host ""

while ($true) {
  try {
    $count = Sync-Once
    $now = Get-Date -Format "HH:mm:ss"
    Send-Status $count "Synced"
    Write-Host "[$now] Synced: $count files" -ForegroundColor Green
  } catch {
    $now = Get-Date -Format "HH:mm:ss"
    Send-Status 0 $_.Exception.Message
    Write-Host "[$now] Sync failed: $($_.Exception.Message)" -ForegroundColor Yellow
  }
  Start-Sleep -Seconds $Interval
}
'''
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('start.bat', start_bat)
        zf.writestr('sync.ps1', sync_ps1.encode('utf-8-sig'))
        zf.writestr('config.json', json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8-sig'))
        zf.writestr('README.txt', readme.encode('utf-8-sig'))
    buf.seek(0)
    return Response(buf.read(), mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename="Bedrock-Live-Sync-{pid}.zip"'})

@app.route('/api/projects/<pid>/build')
def api_build(pid):
    p = DB['projects'].get(pid)
    if not p: return jsonify({'error':'없음'}), 404
    errors, warnings = [], []
    if 'manifest.json' not in p['files']:
        errors.append({'file':'manifest.json','msg':'manifest.json 파일이 없습니다'})
    else:
        try:
            m = json.loads(p['files']['manifest.json'])
            if 'header' not in m: errors.append({'file':'manifest.json','msg':'header 누락'})
            if 'modules' not in m: errors.append({'file':'manifest.json','msg':'modules 누락'})
        except json.JSONDecodeError as e:
            errors.append({'file':'manifest.json','msg':f'JSON 오류: {e}'})
    for path, content in p['files'].items():
        if path.endswith('.json') and path != 'manifest.json':
            try: json.loads(content)
            except: warnings.append({'file':path,'msg':'JSON 형식 확인 필요'})
    return jsonify({'errors': errors, 'warnings': warnings, 
                    'success': len(errors)==0, 'files': len(p['files'])})

def resolve_llm_model(provider, model):
    provider = (provider or 'cobuddy').lower()
    model = (model or 'cobuddy').lower()
    if provider in PROVIDER_MODELS and model in PROVIDER_MODELS[provider]:
        return PROVIDER_MODELS[provider][model]
    if '/' in model:
        return model
    if provider == 'cobuddy':
        return 'baidu/cobuddy:free'
    return 'openai/gpt-4o'

def call_llm_api(messages, provider='cobuddy', model='cobuddy', temperature=0.7, max_tokens=4000, json_mode=False):
    """OpenRouter API 호출 (OpenAI / Baidu CoBuddy)"""
    if not OPENROUTER_API_KEY:
        print('OPENROUTER_API_KEY가 설정되지 않았습니다 (.env 확인)')
        return None
    try:
        headers = {
            'Authorization': f'Bearer {OPENROUTER_API_KEY}',
            'Content-Type': 'application/json',
            'HTTP-Referer': os.environ.get('OPENROUTER_HTTP_REFERER', 'http://localhost:5000'),
            'X-Title': OPENROUTER_APP_NAME,
        }
        resolved = resolve_llm_model(provider, model)
        payload = {
            'model': resolved,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=90)
        response.raise_for_status()
        data = response.json()
        if 'choices' in data and len(data['choices']) > 0:
            return data['choices'][0]['message']['content']
        return None
    except Exception as e:
        print(f'LLM API 오류: {e}')
        if hasattr(e, 'response') and e.response is not None:
            try:
                print('응답 본문:', e.response.text[:500])
            except Exception:
                pass
        return None

def provider_label(provider):
    return {
        'openai': 'OpenAI',
        'cobuddy': 'Baidu Qianfan CoBuddy',
    }.get(provider, provider.title())

def extract_code_block(text, language='javascript'):
    if not text:
        return ''
    if '```' not in text:
        return text.strip()
    for part in text.split('```'):
        if language in part or 'javascript' in part or 'json' in part or part.strip().startswith(('js', 'ts')):
            code = part.split('\n', 1)[1] if '\n' in part else part
            return code.rsplit('```', 1)[0].strip()
    parts = text.split('```')
    if len(parts) >= 3:
        return parts[1].split('\n', 1)[-1].strip()
    return text.strip()

def _strip_json_fences(text):
    text = (text or '').strip()
    for pat in (r'```json\s*([\s\S]*?)```', r'```\s*([\s\S]*?)```'):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return text

def _fix_json_loose(s):
    s = re.sub(r',\s*([}\]])', r'\1', s)
    s = s.replace('\ufeff', '')
    return s

def _find_balanced_json_object(text):
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ''
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None

def _decode_action_content(action):
    if not isinstance(action, dict):
        return ''
    b64 = action.get('content_b64') or action.get('contentBase64') or action.get('content_base64')
    if b64:
        try:
            raw = b64.strip()
            pad = '=' * (-len(raw) % 4)
            return base64.b64decode(raw + pad).decode('utf-8')
        except Exception:
            pass
    for key in ('content', 'text', 'body', 'data', 'source'):
        val = action.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ''

def user_wants_delete(prompt):
    p = (prompt or '').lower()
    keys = ('삭제', '지워', '제거', '없애', 'delete', 'remove', 'drop')
    return any(k in p for k in keys)

def extract_mentioned_paths(prompt, project_files):
    """프롬프트에 언급된 프로젝트 파일 경로 (명시적 요청만)"""
    if not prompt:
        return []
    text = prompt.replace('\\', '/')
    found = []
    keys = list(project_files.keys())
    key_lower = {k.lower(): k for k in keys}

    for m in re.finditer(r'([A-Za-z0-9_./-]+\.(?:js|json|ts))', text, re.I):
        raw = m.group(1).strip().lstrip('/')
        if raw in project_files:
            found.append(raw)
            continue
        low = raw.lower()
        if low in key_lower:
            found.append(key_lower[low])
            continue
        base = raw.split('/')[-1].lower()
        for k in keys:
            if k.lower() == low or k.lower().endswith('/' + base) or k.split('/')[-1].lower() == base:
                found.append(k)
                break

    low = text.lower()
    if 'manifest' in low:
        for k in keys:
            if k.lower() == 'manifest.json' or k.endswith('/manifest.json'):
                if k not in found:
                    found.append(k)
    if 'main.js' in low or '메인' in low:
        for k in keys:
            if k.replace('\\', '/').endswith('scripts/main.js') or k == 'scripts/main.js':
                if k not in found:
                    found.append(k)

    out = []
    for p in found:
        if p not in out:
            out.append(p)
    return out

def infer_new_file_path(prompt):
    """새 파일 경로 추론 (프로젝트에 없는 경로)"""
    if not prompt:
        return None
    patterns = [
        r'([A-Za-z0-9_./-]+\.(?:js|json|ts))\s*(?:파일\s*)?(?:추가|생성|만들|만들어|작성)',
        r'(?:추가|생성|만들|만들어|작성)[^\n]{0,40}?([A-Za-z0-9_./-]+\.(?:js|json|ts))',
        r'(?:새\s*)?파일\s*[`\'"]?([A-Za-z0-9_./-]+\.(?:js|json|ts))',
    ]
    for pat in patterns:
        m = re.search(pat, prompt, re.I)
        if m:
            return m.group(1).replace('\\', '/').strip().lstrip('/')
    return None

def classify_agent_intent(prompt, project_files=None, current_file=None):
    """create_script | fix_error | edit_file | add_file | delete | modify"""
    p = (prompt or '').strip().lower()
    mentioned = extract_mentioned_paths(prompt, project_files or {})

    if user_wants_delete(p):
        return 'delete'
    if infer_new_file_path(prompt) and infer_new_file_path(prompt) not in (project_files or {}):
        return 'add_file'

    fix_keys = ('오류', '에러', '버그', '고쳐', '수정해', 'fix', 'debug', '안돼', '안되', '문제')
    gameplay_keys = ('킥', 'kick', '이벤트', '폭발', '아이템', '엔티티', '블럭', '블록', '플레이어', '스폰')

    if mentioned:
        if any(k in p for k in fix_keys):
            return 'fix_error'
        if any(m.endswith('.json') for m in mentioned) or 'manifest' in p:
            return 'edit_file'
        return 'edit_file'

    if any(k in p for k in fix_keys):
        return 'fix_error'
    if any(k in p for k in gameplay_keys):
        return 'create_script'
    if any(k in p for k in ('작성', '만들', '생성', '구현', '추가', '스크립트')):
        if current_file and current_file.endswith('.json'):
            return 'edit_file'
        return 'create_script'
    return 'modify'

def resolve_agent_targets(intent, project_files, current_file=None, prompt=''):
    """수정 대상 — 명시된 파일만, 다른 파일 건드리지 않음"""
    mentioned = extract_mentioned_paths(prompt, project_files)
    new_path = infer_new_file_path(prompt)

    if intent == 'add_file' and new_path:
        return [new_path]
    if intent == 'delete':
        if mentioned:
            return mentioned[:5]
        if current_file and current_file in project_files:
            return [current_file]
        return []
    if mentioned:
        return mentioned[:3]

    keys = list(project_files.keys())
    js_files = sorted(f for f in keys if f.endswith('.js'))

    if intent == 'fix_error':
        if current_file and current_file in project_files:
            return [current_file]
        if 'scripts/main.js' in project_files:
            return ['scripts/main.js']
        return js_files[:1] if js_files else keys[:1]

    if intent == 'create_script':
        if current_file and current_file.endswith('.js') and current_file in project_files:
            return [current_file]
        if 'scripts/main.js' in project_files:
            return ['scripts/main.js']
        return ['scripts/main.js']

    if intent == 'edit_file':
        if current_file and current_file in project_files:
            return [current_file]
        return keys[:1] if keys else []

    if current_file and current_file in project_files:
        return [current_file]
    return keys[:1] if keys else []

def validate_file_content(path, content):
    """파일 종류에 맞는 내용인지 검사 (manifest를 js에 넣는 실수 방지)"""
    content = (content or '').strip()
    if not content:
        return False, '내용이 비어 있습니다'
    if path.endswith('.json'):
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return False, f'JSON 형식 오류: {e}'
        return True, ''
    if path.endswith('.js'):
        head = content[:800].lower()
        if '"format_version"' in head and 'minecraft:' not in head and 'import ' not in head:
            return False, 'manifest 내용이 .js 파일에 들어갔습니다 — 스크립트만 작성하세요'
        if head.startswith('{') and '"header"' in head and 'import ' not in content[:200]:
            return False, 'JSON manifest가 .js 파일에 들어갔습니다'
    return True, ''

def filter_actions_scope(actions, allowed_paths, project_files):
    """허용된 경로만 적용 (명시 요청 시 다른 파일 보호)"""
    if not allowed_paths:
        return actions
    allowed = set(allowed_paths)
    out = []
    for a in actions or []:
        path = (a.get('path') or '').replace('\\', '/').strip().lstrip('/')
        if a.get('type') == 'delete':
            if path in allowed or path in project_files:
                out.append(a)
            continue
        if path in allowed:
            out.append(a)
    return out

def file_lang_from_path(path):
    if path.endswith('.json'):
        return 'json'
    if path.endswith('.ts'):
        return 'typescript'
    return 'javascript'

def agent_generate_file_content(prompt, path, existing, provider, model, intent='create_script'):
    """에이전트: 단일 파일 전체 내용 생성 — 파일 종류별 프롬프트 분리"""
    language = file_lang_from_path(path)
    is_manifest = path.lower().endswith('manifest.json') or path.lower() == 'manifest.json'

    if is_manifest:
        system_msg = f"""Minecraft Bedrock **Behavior Pack manifest.json** 전문가입니다.
파일 `{path}`만 작성합니다. JavaScript 코드 금지. 유효한 JSON만 출력하세요.
- format_version: 2
- header (name, description, uuid, version, min_engine_version)
- modules: script 모듈, entry는 scripts/main.js 등 프로젝트에 맞게
- dependencies: @minecraft/server 버전
- 기존 uuid가 있으면 유지, 없으면 새 uuid"""
        task = f'manifest.json 요청: {prompt}'
    elif path.endswith('.js'):
        bedrock_hint = """
- import {{ world, system }} from "@minecraft/server";
- 이벤트: world.afterEvents.playerBreakBlock 등
- **이 파일은 .js 스크립트만** — manifest JSON 금지"""
        if intent == 'fix_error':
            system_msg = f"""Bedrock Script API 디버거. `{path}` 오류 수정, **JS 전체** 출력.\n{bedrock_hint}"""
            task = f'오류 수정: {prompt}'
        else:
            system_msg = f"""Bedrock Script API 개발자. `{path}`에 기능 구현, **JS 전체** 출력.\n{bedrock_hint}"""
            task = f'요청: {prompt}'
    else:
        system_msg = f"""파일 `{path}` 내용을 요청에 맞게 작성. 형식: {language}"""
        task = prompt

    user_parts = [task, f'대상 파일(이 파일만 수정): {path}', '다른 파일은 절대 수정하지 마세요.']
    if existing and existing.strip():
        user_parts.append(f'현재 `{path}` 내용:\n```{language}\n{existing[:8000]}\n```')
        user_parts.append('요청 반영 후 이 파일의 **전체 최종 내용**만 출력.')
    else:
        user_parts.append(f'새 파일 `{path}` 입니다.')

    messages = [
        {'role': 'system', 'content': system_msg},
        {'role': 'user', 'content': '\n\n'.join(user_parts)},
    ]
    result = call_llm_api(messages, provider=provider, model=model, temperature=0.4, max_tokens=6000)
    if not result:
        return None
    code = extract_code_block(result, language)
    if not code or not code.strip():
        code = result.strip()
    if path.endswith('.js') and not is_manifest:
        if 'import' not in code and '@minecraft/server' not in code:
            if not (code.strip().startswith('{') and 'format_version' in code):
                code = 'import { world, system } from "@minecraft/server";\n\n' + code
    if is_manifest:
        try:
            obj = json.loads(code)
            code = json.dumps(obj, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    ok, _ = validate_file_content(path, code)
    return code.strip() if ok and code.strip() else None

def run_direct_agent_codegen(prompt, project_files, provider, model, current_file=None):
    """자연어 → 지정 파일만 생성/수정"""
    intent = classify_agent_intent(prompt, project_files, current_file)
    targets = resolve_agent_targets(intent, project_files, current_file, prompt)
    actions = []

    if intent == 'delete':
        for path in targets:
            if path in project_files:
                actions.append({'type': 'delete', 'path': path})
        return actions, intent

    for path in targets[:1]:
        existing = project_files.get(path, '')
        gen_intent = intent if intent != 'add_file' else 'edit_file'
        generated = agent_generate_file_content(
            prompt, path, existing, provider, model, intent=gen_intent,
        )
        if generated:
            ok, err = validate_file_content(path, generated)
            if ok:
                actions.append({
                    'type': 'create' if path not in project_files else 'write',
                    'path': path,
                    'content': generated,
                })
    return actions, intent

def sanitize_agent_actions(actions, prompt, project_files):
    """삭제 차단, 빈 write 제거, write 우선"""
    writes, deletes, other = [], [], []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        act_type = (a.get('type') or a.get('action') or 'write').lower()
        path = (a.get('path') or a.get('file') or '').replace('\\', '/').strip().lstrip('/')
        if not path or '..' in path.split('/'):
            continue
        content = _decode_action_content(a)
        item = {'type': act_type, 'path': path, 'content': content}
        if act_type == 'delete':
            deletes.append(item)
        elif act_type in ('write', 'update', 'edit', 'create', 'add', 'new'):
            if act_type in ('create', 'add', 'new'):
                item['type'] = 'create' if path not in project_files else 'write'
            else:
                item['type'] = 'write'
            writes.append(item)
        else:
            other.append(item)
    return writes + other + deletes

def extract_json_object(text):
    if not text:
        return None
    candidates = [_strip_json_fences(text), text.strip()]
    for raw in candidates:
        for attempt in (raw, _find_balanced_json_object(raw)):
            if not attempt:
                continue
            for variant in (attempt, _fix_json_loose(attempt)):
                try:
                    return json.loads(variant)
                except json.JSONDecodeError:
                    continue
    return None

def fallback_parse_agent_markdown(text, current_file=None):
    """JSON 실패 시 마크다운 코드 블록 → 단일 파일 write"""
    if not text:
        return None
    paths = re.findall(r'(?:path|file)["\']?\s*[:=]\s*["\']?([^\s"\']+\.(?:js|json|ts))', text, re.I)
    target = paths[0] if paths else current_file
    if not target:
        return None
    lang = 'json' if target.endswith('.json') else 'javascript'
    code = extract_code_block(text, lang)
    if not code or len(code) < 3:
        return None
    return {
        'message': f'{target} 파일을 코드 블록에서 적용했습니다.',
        'actions': [{'type': 'write', 'path': target, 'content': code}],
    }

def parse_agent_response(text, current_file=None):
    """에이전트 LLM 응답 → {message, actions}"""
    parsed = extract_json_object(text)
    if not parsed:
        return fallback_parse_agent_markdown(text, current_file)
    actions = parsed.get('actions') or parsed.get('files') or []
    if isinstance(actions, dict):
        actions = [{'type': 'write', 'path': k, 'content': v} for k, v in actions.items()]
    normalized = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        content = _decode_action_content(a)
        normalized.append({
            'type': a.get('type') or a.get('action') or 'write',
            'path': a.get('path') or a.get('file') or '',
            'content': content,
        })
    return {
        'message': parsed.get('message') or parsed.get('summary') or '작업 완료',
        'actions': normalized,
    }

def safe_project_path(path):
    path = (path or '').replace('\\', '/').strip().lstrip('/')
    if not path or '..' in path.split('/'):
        raise ValueError(f'unsafe path: {path}')
    return path

def summarize_project_files(files, max_chars=12000):
    lines = []
    total = 0
    for path in sorted(files.keys()):
        content = files.get(path, '')
        if isinstance(content, str) and content.startswith('data:base64,'):
            snippet = '[binary/base64 file]'
        else:
            snippet = (content or '')[:800]
        block = f'### {path}\n```\n{snippet}\n```'
        if total + len(block) > max_chars:
            lines.append('...(more files omitted)')
            break
        lines.append(block)
        total += len(block)
    return '\n\n'.join(lines)

def apply_agent_actions(project, actions, username):
    applied = []
    errors = []
    for raw in actions or []:
        try:
            act_type = (raw.get('type') or raw.get('action') or '').lower()
            path = safe_project_path(raw.get('path', ''))
            content = raw.get('content')
            if content is None:
                content = _decode_action_content(raw)
            if act_type in ('write', 'update', 'edit', 'create', 'add', 'new'):
                if not can_project(project, username, 'edit_code'):
                    raise PermissionError('edit_code 권한 없음')
                if not (content or '').strip():
                    errors.append({'path': path, 'error': '빈 내용 — 파일을 비우지 않았습니다'})
                    continue
                ok, verr = validate_file_content(path, content)
                if not ok:
                    errors.append({'path': path, 'error': verr})
                    continue
                is_new = path not in project['files']
                project['files'][path] = content
                applied.append({'type': 'create' if is_new else 'write', 'path': path})
            elif act_type == 'delete':
                if not can_project(project, username, 'manage_files'):
                    raise PermissionError('manage_files 권한 없음')
                if path not in project['files']:
                    errors.append({'path': path, 'error': '이미 없는 파일'})
                    continue
                project['files'].pop(path, None)
                applied.append({'type': 'delete', 'path': path})
            elif act_type == 'read':
                applied.append({'type': 'read', 'path': path, 'content': project['files'].get(path, '')[:2000]})
            else:
                errors.append({'path': path, 'error': f'unknown action: {act_type}'})
        except Exception as e:
            errors.append({'path': raw.get('path'), 'error': str(e)})
    return applied, errors

def enrich_agent_actions_with_codegen(actions, prompt, project_files, provider, model, current_file=None):
    """내용이 비어 있으면 LLM으로 파일 전체 생성"""
    intent = classify_agent_intent(prompt, project_files, current_file)
    out = []
    for a in actions:
        act = dict(a)
        if act.get('type') not in ('write', 'create'):
            out.append(act)
            continue
        content = (act.get('content') or '').strip()
        if not content:
            existing = project_files.get(act['path'], '')
            generated = agent_generate_file_content(
                prompt, act['path'], existing, provider, model, intent=intent,
            )
            if generated:
                act['content'] = generated
            else:
                continue
        out.append(act)
    if out:
        return out
    direct, _ = run_direct_agent_codegen(prompt, project_files, provider, model, current_file)
    return direct

@app.route('/api/ai', methods=['POST'])
def api_ai():
    """AI 코드 생성 (통합)"""
    d = request.json
    prompt = d.get('prompt','').strip()
    current_code = d.get('code','')
    language = d.get('language', 'javascript')
    provider = d.get('provider', 'cobuddy')
    model = d.get('model', 'cobuddy')
    
    if not prompt:
        return jsonify({'error': '프롬프트가 없습니다'}), 400
    
    # 시스템 메시지
    system_msg = f"""당신은 Minecraft Bedrock Edition Script API 전문가입니다.
사용자의 요청에 따라 고품질의 코드를 생성하거나 개선합니다.
- 사용 언어: {language}
- Script API 버전: 1.10.0 이상
- 코드는 주석이 포함된 완전한 예제여야 합니다.
- 보안과 성능을 고려하여 작성하세요."""
    
    user_msg = f"요청: {prompt}"
    if current_code:
        user_msg += f"\n\n현재 코드:\n```{language}\n{current_code}\n```"
    
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg}
    ]
    
    result = call_llm_api(messages, provider=provider, model=model, temperature=0.7, max_tokens=2000)
    if result:
        code = extract_code_block(result, language)
        explanation = f"AI가 생성한 코드입니다. (공급자: {provider_label(provider)}, 모델: {resolve_llm_model(provider, model)})"
        return jsonify({'code': code.strip(), 'explanation': explanation, 'provider': provider_label(provider), 'model': resolve_llm_model(provider, model)})
    
    return jsonify({'error': 'AI 응답이 없습니다'}), 500

@app.route('/api/ai/analyze', methods=['POST'])
def api_ai_analyze():
    """코드 분석 및 최적화 제안"""
    d = request.json
    code = d.get('code','')
    language = d.get('language', 'javascript')
    provider = d.get('provider', 'cobuddy')
    model = d.get('model', 'cobuddy')
    
    if not code:
        return jsonify({'error': '분석할 코드가 없습니다'}), 400
    
    messages = [
        {"role": "system", "content": """당신은 코드 리뷰 전문가입니다.
다음 코드를 분석하고:
1. 잠재적 버그나 문제점
2. 성능 최적화 제안
3. 모범 사례 준수 여부
4. 개선 사항

JSON 형식으로 응답하세요:
{
  "issues": ["문제1", "문제2", ...],
  "optimizations": ["최적화1", ...],
  "suggestions": ["제안1", ...]
}"""},
        {"role": "user", "content": f"코드를 분석해주세요:\n```{language}\n{code}\n```"}
    ]
    
    result = call_llm_api(messages, provider=provider, model=model, temperature=0.5, max_tokens=1500)
    if result:
        try:
            if '{' in result:
                json_start = result.find('{')
                json_end = result.rfind('}') + 1
                analysis = json.loads(result[json_start:json_end])
                analysis.update({'provider': provider_label(provider), 'model': resolve_llm_model(provider, model)})
                return jsonify(analysis)
        except:
            pass
        return jsonify({'analysis': result, 'provider': provider_label(provider), 'model': resolve_llm_model(provider, model)})
    
    return jsonify({'error': 'AI 응답이 없습니다'}), 500

@app.route('/api/ai/explain', methods=['POST'])
def api_ai_explain():
    """코드 설명"""
    d = request.json
    code = d.get('code','')
    language = d.get('language', 'javascript')
    provider = d.get('provider', 'cobuddy')
    model = d.get('model', 'cobuddy')
    
    if not code:
        return jsonify({'error': '설명할 코드가 없습니다'}), 400
    
    messages = [
        {"role": "system", "content": """당신은 프로그래밍 교육자입니다.
코드를 명확하고 이해하기 쉽게 설명하세요.
- 각 부분의 역할 설명
- 사용된 개념과 기법
- 주의할 점"""},
        {"role": "user", "content": f"다음 코드를 설명해주세요:\n```{language}\n{code}\n```"}
    ]
    
    result = call_llm_api(messages, provider=provider, model=model, temperature=0.5, max_tokens=1500)
    if result:
        return jsonify({'explanation': result, 'provider': provider_label(provider), 'model': resolve_llm_model(provider, model)})
    
    return jsonify({'error': 'AI 응답이 없습니다'}), 500

@app.route('/api/ai/fix', methods=['POST'])
def api_ai_fix():
    """버그 수정 제안"""
    d = request.json
    code = d.get('code','')
    language = d.get('language', 'javascript')
    issue = d.get('issue','')
    provider = d.get('provider', 'cobuddy')
    model = d.get('model', 'cobuddy')
    
    if not code:
        return jsonify({'error': '수정할 코드가 없습니다'}), 400
    
    messages = [
        {"role": "system", "content": f"""당신은 디버깅 전문가입니다.
주어진 코드의 문제를 찾고 수정된 코드를 제공하세요.
- 수정된 완전한 코드 제공
- 변경 사항 설명
- 언어: {language}"""},
        {"role": "user", "content": f"이 코드를 수정해주세요:\n문제: {issue or '오류 발생'}\n```{language}\n{code}\n```"}
    ]
    
    result = call_llm_api(messages, provider=provider, model=model, temperature=0.7, max_tokens=2000)
    if result:
        code_fixed = extract_code_block(result, language)
        return jsonify({'code': code_fixed.strip(), 'explanation': '수정된 코드입니다', 'provider': provider_label(provider), 'model': resolve_llm_model(provider, model)})
    
    return jsonify({'error': 'AI 응답이 없습니다'}), 500

@app.route('/api/ai/agent', methods=['POST'])
def api_ai_agent():
    """프로젝트 파일 읽기/수정/추가 에이전트"""
    u = require_login()
    if not u:
        return jsonify({'error': '로그인 필요'}), 401
    d = request.json or {}
    pid = d.get('project_id', '').strip()
    prompt = d.get('prompt', '').strip()
    current_file = d.get('current_file', '')
    provider = d.get('provider', 'cobuddy')
    model = d.get('model', 'cobuddy')
    history = d.get('history', [])

    if not pid or not prompt:
        return jsonify({'error': 'project_id와 prompt가 필요합니다'}), 400

    p = DB['projects'].get(pid)
    if not p:
        return jsonify({'error': '프로젝트 없음'}), 404
    if not can_project(p, u, 'edit_code'):
        return jsonify({'error': '코드 수정 권한이 없습니다'}), 403

    files_snapshot = dict(p.get('files', {}))
    mentioned = extract_mentioned_paths(prompt, files_snapshot)
    intent = classify_agent_intent(prompt, files_snapshot, current_file)
    intent_labels = {
        'create_script': '스크립트 작성',
        'fix_error': '오류 수정',
        'edit_file': '파일 수정',
        'add_file': '파일 추가',
        'modify': '코드 수정',
        'delete': '삭제',
    }

    use_direct = intent in ('create_script', 'fix_error', 'edit_file', 'add_file', 'delete') or bool(mentioned)

    if use_direct:
        final_actions, intent = run_direct_agent_codegen(
            prompt, files_snapshot, provider, model, current_file,
        )
        scope = mentioned or resolve_agent_targets(intent, files_snapshot, current_file, prompt)
        if scope and intent != 'delete':
            final_actions = filter_actions_scope(final_actions, scope, files_snapshot)
        if intent == 'delete' and not final_actions:
            return jsonify({
                'error': '삭제할 파일을 지정해 주세요. 예: test.js 삭제해줘',
            }), 400
        if final_actions:
            applied, errors = apply_agent_actions(p, final_actions, u)
            if applied:
                save_db()
                for act in applied:
                    if act['type'] in ('write', 'create'):
                        socketio.emit('file_updated', {
                            'path': act['path'],
                            'content': p['files'].get(act['path'], ''),
                            'user': u,
                        }, room=pid)
                        emit_bridge(pid, 'bridge_file_updated', {
                            'path': act['path'],
                            'content': p['files'].get(act['path'], ''),
                            'user': u, 'time': time.time(),
                        })
            write_count = sum(1 for a in applied if a.get('type') in ('write', 'create'))
            paths = ', '.join(a['path'] for a in applied if a.get('type') in ('write', 'create'))
            return jsonify({
                'ok': bool(applied),
                'intent': intent,
                'message': f'{intent_labels.get(intent, intent)} 완료 · {paths}',
                'actions': final_actions,
                'applied': applied,
                'errors': errors,
                'files': p['files'] if applied else None,
                'provider': provider_label(provider),
                'model': resolve_llm_model(provider, model),
            })

    file_tree = summarize_project_files(files_snapshot)
    current_content = ''
    if current_file and current_file in files_snapshot:
        current_content = (files_snapshot[current_file] or '')[:4000]

    system_msg = """당신은 Minecraft Bedrock Script API 코딩 에이전트(CoBuddy)입니다.
사용자 요청에 맞게 파일 **내용을 작성·수정**합니다.

출력: 유효한 JSON 하나만 (마크다운 금지):
{
  "message": "한국어 요약",
  "actions": [
    {"type": "write", "path": "scripts/main.js", "content": "파일 전체 소스코드 문자열"}
  ]
}

규칙:
- **요청에 언급된 파일만** actions에 넣기 (다른 파일 수정·삭제 금지)
- manifest.json → JSON만, .js → JavaScript만 (형식 혼동 금지)
- write/create: 해당 파일 **전체 최종 내용**, 빈 문자열 금지
- delete: 사용자가 삭제를 요청한 파일만
- 새 경로는 type create
- path는 프로젝트 루트 기준, / 사용"""

    code_block = f'\n```\n{current_content}\n```\n' if current_content else ''
    user_msg = f"""의도: {intent_labels.get(intent, intent)}
요청: {prompt}

현재 열린 파일: {current_file or '(없음)'}
{code_block}
프로젝트 파일:
{file_tree}
"""

    messages = [{"role": "system", "content": system_msg}]
    for h in (history or [])[-6:]:
        role = h.get('role', 'user')
        if role in ('user', 'assistant') and h.get('content'):
            messages.append({"role": role, "content": h['content']})
    messages.append({"role": "user", "content": user_msg})

    agent_data = None
    last_raw = None
    result = None
    for attempt in range(3):
        use_json_mode = attempt == 0
        result = call_llm_api(
            messages, provider=provider, model=model,
            temperature=0.2, max_tokens=8000, json_mode=use_json_mode,
        )
        if not result and use_json_mode:
            result = call_llm_api(
                messages, provider=provider, model=model,
                temperature=0.2, max_tokens=8000, json_mode=False,
            )
        if not result:
            continue
        last_raw = result
        agent_data = parse_agent_response(result, current_file)
        if agent_data and agent_data.get('actions'):
            break
        messages.append({"role": "assistant", "content": result[:4000]})
        messages.append({
            "role": "user",
            "content": (
                "JSON이 잘못되었거나 actions가 비었습니다. delete 없이 write만 사용하고, "
                "각 파일의 전체 content를 채워서 JSON만 다시 출력하세요."
            ),
        })

    if not result:
        return jsonify({'error': 'AI 응답이 없습니다. API 키(.env)를 확인하세요.'}), 500

    raw_actions = (agent_data or {}).get('actions', []) if agent_data else []
    if not raw_actions:
        raw_actions = []

    sanitized = sanitize_agent_actions(raw_actions, prompt, files_snapshot)
    scope = mentioned or resolve_agent_targets(intent, files_snapshot, current_file, prompt)
    if scope:
        sanitized = filter_actions_scope(sanitized, scope, files_snapshot)
    final_actions = enrich_agent_actions_with_codegen(
        sanitized, prompt, files_snapshot, provider, model, current_file,
    )
    if scope and intent != 'delete':
        final_actions = filter_actions_scope(final_actions, scope, files_snapshot)

    if not final_actions and not agent_data:
        print(f'[agent] parse failed, raw[:800]={(last_raw or "")[:800]}')
        final_actions = enrich_agent_actions_with_codegen(
            [], prompt, files_snapshot, provider, model, current_file,
        )

    if not final_actions:
        return jsonify({
            'error': '코드를 생성하지 못했습니다. 요청을 더 구체적으로 적어 주세요.',
            'message': (agent_data or {}).get('message') or (last_raw or '')[:500],
            'actions': raw_actions,
            'applied': [],
            'errors': [{'error': '적용 가능한 write/create 없음'}],
            'provider': provider_label(provider),
            'model': resolve_llm_model(provider, model),
        }), 422

    applied, errors = apply_agent_actions(p, final_actions, u)
    if applied:
        save_db()
        for act in applied:
            if act['type'] in ('write', 'create'):
                socketio.emit('file_updated', {
                    'path': act['path'],
                    'content': p['files'].get(act['path'], ''),
                    'user': u,
                }, room=pid)
                emit_bridge(pid, 'bridge_file_updated', {
                    'path': act['path'],
                    'content': p['files'].get(act['path'], ''),
                    'user': u,
                    'time': time.time(),
                })
            elif act['type'] == 'delete':
                socketio.emit('file_deleted', {'path': act['path'], 'user': u}, room=pid)
                emit_bridge(pid, 'bridge_file_deleted', {'path': act['path'], 'user': u, 'time': time.time()})

    if not applied and not errors:
        errors = [{'error': '적용할 파일 변경이 없습니다'}]

    write_count = sum(1 for a in applied if a.get('type') in ('write', 'create'))
    del_count = sum(1 for a in applied if a.get('type') == 'delete')
    summary = (agent_data or {}).get('message', '작업 완료')
    if write_count:
        summary = f'{write_count}개 파일 수정 · ' + summary
    if del_count:
        summary += f' ({del_count}개 삭제)'

    return jsonify({
        'ok': bool(applied),
        'intent': intent,
        'message': summary,
        'actions': final_actions,
        'applied': applied,
        'errors': errors,
        'files': p['files'] if applied else None,
        'provider': provider_label(provider),
        'model': resolve_llm_model(provider, model),
    })

@app.route('/api/users/<username>')
def api_user(username):
    u = DB['users'].get(username)
    if not u: return jsonify({'error':'없음'}), 404
    info = public_user(username)
    info['projects'] = [{'id':pid,'name':p['name'],'thumbnail':p['thumbnail'],
                        'likes':len(p['likes']),'downloads':p['downloads']}
                       for pid,p in DB['projects'].items() 
                       if p['owner']==username and p['public']]
    return jsonify({'user': info})

@app.route('/api/friends')
def api_friends():
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    friends = []
    for username in DB['users'][u].get('following', []):
        info = public_user(username)
        if not info: continue
        info['mutual'] = u in info.get('following', [])
        friends.append(info)
    friends.sort(key=lambda x: (not x['online'], x['username'].lower()))
    return jsonify({'friends': friends})

@app.route('/api/friends/add', methods=['POST'])
def api_add_friend():
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    username = (request.json or {}).get('username', '').strip()
    if not username or username == u:
        return jsonify({'error':'친구 아이디를 확인해주세요'}), 400
    target = DB['users'].get(username)
    if not target:
        return jsonify({'error':'사용자를 찾을 수 없습니다'}), 404
    if username not in DB['users'][u]['following']:
        DB['users'][u]['following'].append(username)
    if u not in target['followers']:
        target['followers'].append(u)
    save_db()
    return jsonify({'ok': True, 'friend': public_user(username)})

@app.route('/api/friends/<username>', methods=['DELETE'])
def api_remove_friend(username):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    if username in DB['users'][u]['following']:
        DB['users'][u]['following'].remove(username)
    target = DB['users'].get(username)
    if target and u in target['followers']:
        target['followers'].remove(u)
    save_db()
    return jsonify({'ok': True})

@app.route('/api/projects/<pid>/invite', methods=['POST'])
def api_invite_project(pid):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p: return jsonify({'error':'프로젝트 없음'}), 404
    if not can_project(p, u, 'invite_members'):
        return jsonify({'error':'초대 권한 없음'}), 403
    d = request.json or {}
    username = d.get('username', '').strip()
    role = d.get('role', 'developer')
    if role not in ('admin', 'developer', 'viewer'): role = 'developer'
    if role == 'admin' and not can_project(p, u, 'manage_roles'):
        return jsonify({'error':'admin 초대는 관리자만 가능합니다'}), 403
    if username not in DB['users']:
        return jsonify({'error':'사용자를 찾을 수 없습니다'}), 404
    if username not in DB['users'][u].get('following', []):
        return jsonify({'error':'친구만 초대할 수 있습니다'}), 400
    p['members'][username] = role
    p.setdefault('member_permissions', {})[username] = PERMISSION_DEFAULTS[role].copy()
    save_db()
    payload = {'project_id': pid, 'project_name': p['name'], 'from': u, 'role': role}
    socketio.emit('project_invite', payload, room=f'user:{username}')
    socketio.emit('member_added', {'username': username, 'role': role}, room=pid)
    return jsonify({'ok': True, 'member': username, 'role': role})

@app.route('/api/projects/<pid>/members/<username>', methods=['POST'])
def api_update_member(pid, username):
    u = require_login()
    if not u: return jsonify({'error':'로그인 필요'}), 401
    p = DB['projects'].get(pid)
    if not p: return jsonify({'error':'프로젝트 없음'}), 404
    if not can_project(p, u, 'manage_roles'):
        return jsonify({'error':'관리자만 권한을 바꿀 수 있습니다'}), 403
    if username not in p['members']:
        return jsonify({'error':'멤버가 아닙니다'}), 404
    d = request.json or {}
    role = d.get('role')
    if role not in ('admin', 'developer', 'viewer'):
        return jsonify({'error':'권한 값 오류'}), 400
    if username == p['owner'] and role != 'admin':
        return jsonify({'error':'프로젝트 소유자는 admin이어야 합니다'}), 400
    p['members'][username] = role
    permissions = d.get('permissions')
    if isinstance(permissions, dict):
        allowed = PERMISSION_DEFAULTS[role].copy()
        for key in allowed:
            if key in permissions:
                allowed[key] = bool(permissions[key])
        if username == p['owner']:
            allowed.update(PERMISSION_DEFAULTS['admin'])
        p.setdefault('member_permissions', {})[username] = allowed
    else:
        p.setdefault('member_permissions', {})[username] = member_permissions(p, username)
    save_db()
    perms = member_permissions(p, username)
    socketio.emit('member_role_updated', {'username': username, 'role': role, 'permissions': perms}, room=pid)
    return jsonify({'ok': True, 'username': username, 'role': role, 'permissions': perms})

@app.route('/api/users/<username>/follow', methods=['POST'])
def api_follow(username):
    u = require_login()
    if not u or u==username: return jsonify({'error':'권한'}), 401
    target = DB['users'].get(username)
    if not target: return jsonify({'error':'없음'}), 404
    if u in target['followers']:
        target['followers'].remove(u)
        if username in DB['users'][u]['following']:
            DB['users'][u]['following'].remove(username)
    else:
        target['followers'].append(u)
        DB['users'][u]['following'].append(username)
    save_db()
    return jsonify({'followers': len(target['followers']), 'following': u in target['followers']})

# ==================== SocketIO ====================
@socketio.on('connect')
def on_connect():
    u = current_user()
    if not u: return
    DB['active_users'][u].add(request.sid)
    DB['socket_users'][request.sid] = u
    join_room(f'user:{u}')

@socketio.on('disconnect')
def on_disconnect():
    u = DB['socket_users'].pop(request.sid, None)
    pid = DB['socket_projects'].pop(request.sid, None)
    if pid and u:
        if pid in DB['online_users']:
            DB['online_users'][pid].pop(u, None)
            emit('online_users', _online_payload(pid), room=pid)
        if pid in DB['editing_status']:
            for f in list(DB['editing_status'][pid].keys()):
                DB['editing_status'][pid][f].pop(u, None)
                if not DB['editing_status'][pid][f]:
                    DB['editing_status'][pid].pop(f, None)
            emit('editing_status', DB['editing_status'][pid], room=pid)
    if not u: return
    DB['active_users'][u].discard(request.sid)
    if not DB['active_users'][u]:
        DB['active_users'].pop(u, None)

@socketio.on('join_project')
def on_join(data):
    pid = data['pid']
    u = current_user() or data.get('user','guest')
    join_room(pid)
    DB['socket_projects'][request.sid] = pid
    if pid not in DB['online_users']: DB['online_users'][pid] = {}
    DB['online_users'][pid][u] = {'file': None, 'line': 0, 'col': 0, 'selection': None}
    if pid not in DB['editing_status']: DB['editing_status'][pid] = {}
    emit('online_users', _online_payload(pid), room=pid)
    emit('editing_status', DB['editing_status'][pid], room=pid)

@socketio.on('bridge_join')
def on_bridge_join(data):
    pid = data.get('pid')
    token = data.get('token')
    p = DB['projects'].get(pid)
    if not p or ensure_bridge_token(p) != token:
        emit('bridge_error', {'error': 'bridge 인증 실패'})
        return
    join_room(bridge_room(pid))
    emit('bridge_ready', {'project': p['name'], 'files': len(p['files'])})

def _online_payload(pid):
    users = DB['online_users'].get(pid, {})
    return [{'user':u, **info} for u,info in users.items()]

@socketio.on('leave_project')
def on_leave(data):
    pid = data['pid']
    u = current_user() or data.get('user','guest')
    leave_room(pid)
    DB['socket_projects'].pop(request.sid, None)
    if pid in DB['online_users']:
        DB['online_users'][pid].pop(u, None)
        emit('online_users', _online_payload(pid), room=pid)
    # 편집 중 표시 제거
    if pid in DB['editing_status']:
        for f in list(DB['editing_status'][pid].keys()):
            DB['editing_status'][pid][f].pop(u, None)
            if not DB['editing_status'][pid][f]:
                DB['editing_status'][pid].pop(f, None)
        emit('editing_status', DB['editing_status'][pid], room=pid)

@socketio.on('cursor_move')
def on_cursor(data):
    pid = data['pid']
    u = current_user() or data.get('user','guest')
    if pid in DB['online_users'] and u in DB['online_users'][pid]:
        DB['online_users'][pid][u].update({
            'file': data.get('file'),
            'line': data.get('line'),
            'col': data.get('col'),
            'selection': data.get('selection'),
            'editingLine': data.get('editingLine'),
        })
    emit('cursor_update', {
        'user': u, 'file': data.get('file'), 
        'line': data.get('line'), 'col': data.get('col'),
        'selection': data.get('selection'),
        'editingLine': data.get('editingLine'),
    }, room=pid, include_self=False)

@socketio.on('editing_file')
def on_editing(data):
    """파일을 편집 중임을 알림"""
    pid = data['pid']
    file = data.get('file')
    u = current_user() or data.get('user','guest')
    if pid not in DB['editing_status']: DB['editing_status'][pid] = {}
    
    # 기존 편집 상태 제거
    for f in list(DB['editing_status'][pid].keys()):
        if u in DB['editing_status'][pid][f]:
            del DB['editing_status'][pid][f][u]
            if not DB['editing_status'][pid][f]:
                del DB['editing_status'][pid][f]
    
    # 새 파일 편집 중 표시
    if file:
        if file not in DB['editing_status'][pid]:
            DB['editing_status'][pid][file] = {}
        DB['editing_status'][pid][file][u] = time.time()
    
    emit('editing_status', DB['editing_status'][pid], room=pid)

@socketio.on('code_change')
def on_code_change(data):
    pid = data['pid']
    u = current_user() or data.get('user','guest')
    p = DB['projects'].get(pid)
    if p and can_project(p, u, 'edit_code'):
        p['files'][data['file']] = data['content']
        save_db()
        socketio.emit('code_updated', {
            'file':data['file'], 
            'content':data['content'], 
            'user': u,
            'changes': data.get('changes'),  # Monaco 변경 정보
            'editingLine': data.get('editingLine'),
        }, room=pid)
        emit_bridge(pid, 'bridge_file_updated', {
            'path': data['file'],
            'content': data['content'],
            'user': u,
            'time': time.time(),
        })

@socketio.on('send_chat')
def on_chat(data):
    pid = data['pid']
    u = current_user() or data.get('user','guest')
    msg = {'user': u, 'msg': data['msg'], 'time': time.time()}
    p = DB['projects'].get(pid)
    if p:
        p['chat'].append(msg)
        if len(p['chat']) > 500: p['chat'] = p['chat'][-500:]
        emit('new_chat', msg, room=pid)

# ==================== HTML ====================
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MineScript Hub</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/loader.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700;800&family=VT323&family=Fira+Code&display=swap" rel="stylesheet">
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<style>
:root {
  --bg:#0a0e1a; --bg2:#0f1422; --bg3:#161c2e;
  --neon:#00d4ff; --neon2:#0099cc; --purple:#a855f7; --green:#00ff88; --pink:#ff3b8b;
  --text:#e4e9f5; --text2:#8b94b3; --border:rgba(0,212,255,.15);
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;margin:0;overflow-x:hidden}
.bg-grid{position:fixed;inset:0;z-index:-2;pointer-events:none;
  background-image:linear-gradient(rgba(0,212,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(0,212,255,.04) 1px,transparent 1px);
  background-size:50px 50px}
.bg-glow{position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:radial-gradient(ellipse at 20% 0%,rgba(0,212,255,.15),transparent 50%),radial-gradient(ellipse at 80% 100%,rgba(168,85,247,.12),transparent 50%)}
.glass{background:rgba(22,28,46,.5);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid var(--border)}
.glass-strong{background:rgba(15,20,34,.92);backdrop-filter:blur(24px);border:1px solid var(--border)}
.mc-btn{font-family:'VT323',monospace;font-size:18px;padding:8px 20px;
  background:linear-gradient(180deg,#4a8cff 0%,#2563eb 100%);
  border:2px solid #000;color:white;text-shadow:2px 2px 0 rgba(0,0,0,.5);cursor:pointer;
  box-shadow:inset -2px -2px 0 rgba(0,0,0,.4),inset 2px 2px 0 rgba(255,255,255,.3),0 0 20px rgba(0,212,255,.3);
  transition:all .1s;letter-spacing:1px}
.mc-btn:hover{box-shadow:inset -2px -2px 0 rgba(0,0,0,.4),inset 2px 2px 0 rgba(255,255,255,.3),0 0 30px rgba(0,212,255,.6)}
.mc-btn:active{box-shadow:inset 2px 2px 0 rgba(0,0,0,.4);transform:translateY(1px)}
.neon-btn{background:linear-gradient(135deg,rgba(0,212,255,.12),rgba(0,212,255,.05));border:1px solid var(--neon);
  color:var(--neon);padding:8px 18px;border-radius:8px;font-weight:600;cursor:pointer;transition:all .25s;font-size:14px;display:inline-flex;align-items:center;gap:6px}
.neon-btn:hover{background:linear-gradient(135deg,rgba(0,212,255,.25),rgba(0,212,255,.1));box-shadow:0 0 25px rgba(0,212,255,.4);transform:translateY(-1px)}
.neon-btn.purple{border-color:var(--purple);color:var(--purple);background:linear-gradient(135deg,rgba(168,85,247,.12),rgba(168,85,247,.05))}
.neon-btn.purple:hover{box-shadow:0 0 25px rgba(168,85,247,.4)}
.neon-btn.pink{border-color:var(--pink);color:var(--pink);background:linear-gradient(135deg,rgba(255,59,139,.12),rgba(255,59,139,.05))}
.neon-btn.green{border-color:var(--green);color:var(--green);background:linear-gradient(135deg,rgba(0,255,136,.12),rgba(0,255,136,.05))}
.card{background:rgba(22,28,46,.5);backdrop-filter:blur(20px);border:1px solid var(--border);border-radius:14px;
  transition:all .3s;overflow:hidden}
.card:hover{border-color:rgba(0,212,255,.5);transform:translateY(-3px);box-shadow:0 10px 40px rgba(0,212,255,.15)}
.tag{display:inline-block;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:600;
  background:rgba(0,212,255,.1);color:var(--neon);border:1px solid rgba(0,212,255,.3);margin:2px}
.input{width:100%;padding:10px 14px;background:rgba(10,14,26,.6);border:1px solid var(--border);
  border-radius:8px;color:var(--text);outline:none;transition:all .2s;font-family:inherit;font-size:14px}
.input:focus{border-color:var(--neon);box-shadow:0 0 0 3px rgba(0,212,255,.15)}
.sidebar-item{display:flex;align-items:center;gap:12px;padding:11px 14px;border-radius:10px;cursor:pointer;
  transition:all .2s;color:var(--text2);font-weight:500;font-size:14px;position:relative}
.sidebar-item:hover{background:rgba(0,212,255,.06);color:var(--text)}
.sidebar-item.active{background:linear-gradient(135deg,rgba(0,212,255,.15),rgba(0,212,255,.05));color:var(--neon)}
.sidebar-item.active::before{content:'';position:absolute;left:0;top:8px;bottom:8px;width:3px;
  background:var(--neon);border-radius:0 4px 4px 0;box-shadow:0 0 12px var(--neon)}
.scrollbar::-webkit-scrollbar{width:8px;height:8px}
.scrollbar::-webkit-scrollbar-track{background:transparent}
.scrollbar::-webkit-scrollbar-thumb{background:rgba(0,212,255,.2);border-radius:4px}
.scrollbar::-webkit-scrollbar-thumb:hover{background:rgba(0,212,255,.4)}
.gradient-text{background:linear-gradient(135deg,#00d4ff,#a855f7);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.glow-text{text-shadow:0 0 20px rgba(0,212,255,.6)}
.fade-in{animation:fadeIn .4s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.file-tree-item{padding:8px 10px;cursor:pointer;border-radius:6px;font-size:13px;display:flex;align-items:center;gap:8px;color:#c7d2fe;position:relative;user-select:none;transition:background .15s,color .15s}
.file-tree-item.folder{font-weight:700;color:#cbd5e1;background:transparent}
.file-tree-item:hover{background:rgba(0,212,255,.08);color:#fff}
.file-tree-item.active{background:rgba(0,212,255,.16);color:var(--neon)}
.file-tree-item .editing-badge{position:absolute;right:4px;display:flex;gap:4px}
.file-actions{display:flex;gap:4px;opacity:0;transition:opacity .2s}
.file-tree-item:hover .file-actions{opacity:1}
.file-actions i{width:18px;height:18px;display:flex;align-items:center;justify-content:center;color:#94a3b8}
.file-actions i:hover{color:#ffffff}
.editing-dot{width:6px;height:6px;border-radius:50%;animation:pulse-dot 1.5s infinite}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.4}}
.tab{padding:8px 16px;background:#1a2138;border-right:1px solid #0a0e1a;display:flex;align-items:center;gap:8px;
  cursor:pointer;font-size:13px;color:#9ca3af;border-bottom:2px solid transparent;white-space:nowrap}
.tab.active{background:#0a0e1a;color:#fff;border-bottom-color:var(--neon)}
.tab:hover{color:#fff}
.tab .dirty{width:8px;height:8px;border-radius:50%;background:var(--neon)}
.chat-msg{padding:6px 12px;margin:3px 0;border-radius:8px;animation:fadeIn .3s}
.chat-msg:hover{background:rgba(0,212,255,.04)}
.online-dot{display:inline-block;width:8px;height:8px;background:var(--green);border-radius:50%;box-shadow:0 0 8px var(--green)}
.role-admin{background:linear-gradient(135deg,#ff3b8b,#a855f7);color:white;padding:2px 8px;border-radius:5px;font-size:10px;font-weight:700}
.role-developer{background:linear-gradient(135deg,#00d4ff,#0099cc);color:white;padding:2px 8px;border-radius:5px;font-size:10px;font-weight:700}
.role-viewer{background:rgba(139,148,179,.3);color:#8b94b3;padding:2px 8px;border-radius:5px;font-size:10px;font-weight:700}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(8px);z-index:100;
  display:flex;align-items:center;justify-content:center;animation:fadeIn .2s}
@media (max-width:768px){
  #sidebar{position:fixed;left:-260px;top:0;bottom:0;z-index:50;transition:left .3s}
  #sidebar.open{left:0}
  #main-content{margin-left:0!important}
}
.mc-block{width:50px;height:50px;background:linear-gradient(135deg,#7cb342 0%,#558b2f 100%);
  border:2px solid #000;display:inline-block;image-rendering:pixelated;position:relative;
  box-shadow:inset -3px -3px 0 rgba(0,0,0,.3),inset 3px 3px 0 rgba(255,255,255,.2)}
.shake-on-hover:hover{animation:shake .4s}
@keyframes shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-3deg)}75%{transform:rotate(3deg)}}
.hero-text{font-size:clamp(2rem,5vw,4.5rem);font-weight:800;line-height:1.1}
.notification{position:fixed;top:20px;right:20px;z-index:200;padding:14px 22px;border-radius:10px;
  background:rgba(15,20,34,.95);border:1px solid var(--neon);color:white;
  box-shadow:0 10px 40px rgba(0,212,255,.4);animation:slideInRight .3s;max-width:350px}
@keyframes slideInRight{from{transform:translateX(400px)}to{transform:none}}
.context-menu{position:fixed;z-index:1000;min-width:180px;background:rgba(15,20,34,.98);
  border:1px solid var(--border);border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.5);padding:4px}
.context-menu-item{padding:8px 12px;cursor:pointer;border-radius:5px;font-size:13px;display:flex;align-items:center;gap:8px;color:var(--text)}
.context-menu-item:hover{background:rgba(0,212,255,.15);color:var(--neon)}
.context-menu-item.danger{color:var(--pink)}
.context-menu-item.danger:hover{background:rgba(255,59,139,.15)}
.context-divider{height:1px;background:var(--border);margin:4px 0}

/* Monaco: 협업 커서 색상 */
.collab-cursor-0 { border-left: 3px solid #00d4ff !important; }
.collab-cursor-1 { border-left: 3px solid #ff3b8b !important; }
.collab-cursor-2 { border-left: 3px solid #00ff88 !important; }
.collab-cursor-3 { border-left: 3px solid #ffaa00 !important; }
.collab-cursor-4 { border-left: 3px solid #a855f7 !important; }
.collab-selection-0 { background-color: rgba(0,212,255,.25) !important; }
.collab-selection-1 { background-color: rgba(255,59,139,.25) !important; }
.collab-selection-2 { background-color: rgba(0,255,136,.25) !important; }
.collab-selection-3 { background-color: rgba(255,170,0,.25) !important; }
.collab-selection-4 { background-color: rgba(168,85,247,.25) !important; }
.collab-editing-line-0 { background: rgba(0,212,255,.12) !important; border-left: 3px solid #00d4ff; }
.collab-editing-line-1 { background: rgba(255,59,139,.12) !important; border-left: 3px solid #ff3b8b; }
.collab-editing-line-2 { background: rgba(0,255,136,.12) !important; border-left: 3px solid #00ff88; }
.collab-editing-line-3 { background: rgba(255,170,0,.12) !important; border-left: 3px solid #ffaa00; }
.collab-editing-line-4 { background: rgba(168,85,247,.12) !important; border-left: 3px solid #a855f7; }
.collab-name-tag{position:absolute;padding:2px 7px;border-radius:5px;color:#06111f;font-size:11px;font-weight:800;box-shadow:0 2px 10px rgba(0,0,0,.28);white-space:nowrap;transform:translateY(-22px);z-index:50;pointer-events:none}

/* 저장된 줄 (초록) / 수정 중 (노랑) 거터 */
.saved-line-glyph { background: var(--green) !important; width: 4px !important; margin-left: 3px; }
.modified-line-glyph { background: #ffaa00 !important; width: 4px !important; margin-left: 3px; }

.editing-indicator{display:inline-flex;align-items:center;gap:4px;font-size:10px;padding:3px 7px;border-radius:8px;background:rgba(255,170,0,.25);color:#fbbf24;border:1px solid rgba(255,170,0,.5);font-weight:600;white-space:nowrap}

/* editing dot 애니메이션 */
.editing-dot{width:8px;height:8px;border-radius:50%;animation:pulse-editing-dot 1.2s infinite;flex-shrink:0}
@keyframes pulse-editing-dot{0%,100%{opacity:1;transform:scale(1)}50%{opacity:0.6;transform:scale(0.85)}}

/* 드래그 오버 효과 */
.drag-over{border:2px dashed var(--neon)!important;background:rgba(0,212,255,.08)!important}

/* 토글 스위치 */
.switch{position:relative;display:inline-block;width:40px;height:22px}
.switch input{display:none}
.switch .slider{position:absolute;cursor:pointer;inset:0;background:#1a2138;border:1px solid var(--border);border-radius:22px;transition:.3s}
.switch .slider:before{content:'';position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:white;border-radius:50%;transition:.3s}
.switch input:checked + .slider{background:linear-gradient(135deg,#00d4ff,#0099cc);border-color:var(--neon)}
.switch input:checked + .slider:before{transform:translateX(18px)}

select.input{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%2300d4ff'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center;background-size:14px;padding-right:32px}

/* AI 패널 — CV Code / Cursor 스타일 */
#ai-panel{position:fixed;top:0;right:0;width:min(440px,100vw);height:100vh;z-index:200;
  background:#0d1117;border-left:1px solid #21262d;box-shadow:-8px 0 32px rgba(0,0,0,.5);
  display:flex;flex-direction:column;transform:translateX(100%);transition:transform .28s cubic-bezier(.4,0,.2,1)}
#ai-panel.open{transform:translateX(0)}
.ai-panel-header{padding:12px 16px;border-bottom:1px solid #21262d;background:linear-gradient(180deg,#161b22,#0d1117)}
.ai-panel-title{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:#58a6ff}
.ai-panel-title .brand{color:#3fb950}
.ai-tab-bar{display:flex;gap:2px;padding:8px 12px 0;border-bottom:1px solid #21262d;background:#0d1117;overflow-x:auto}
.ai-tab{padding:8px 12px;border-radius:6px 6px 0 0;background:transparent;color:#8b949e;cursor:pointer;border:none;
  font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;transition:all .15s;border-bottom:2px solid transparent;white-space:nowrap}
.ai-tab:hover{color:#c9d1d9;background:#161b22}
.ai-tab.active{color:#58a6ff;border-bottom-color:#58a6ff;background:#161b22}
.ai-tab-content{display:block;flex:1;overflow:hidden}
.ai-tab-content.hidden{display:none}
.ai-panel-body{flex:1;overflow-y:auto;padding:12px;scrollbar-width:thin}
.ai-code-input{width:100%;background:#010409;border:1px solid #30363d;border-radius:8px;color:#c9d1d9;
  font-family:'JetBrains Mono',monospace;font-size:12px;padding:12px;resize:vertical;min-height:88px;line-height:1.5}
.ai-code-input:focus{outline:none;border-color:#58a6ff;box-shadow:0 0 0 2px rgba(88,166,255,.2)}
.ai-code-input::placeholder{color:#484f58}
.ai-agent-chat{display:flex;flex-direction:column;gap:10px;max-height:calc(100vh - 320px);overflow-y:auto}
.ai-msg{padding:10px 12px;border-radius:8px;font-size:12px;line-height:1.55;font-family:'Inter',sans-serif}
.ai-msg.user{background:#161b22;border:1px solid #30363d;color:#c9d1d9;margin-left:24px}
.ai-msg.assistant{background:#0d1117;border:1px solid #238636;color:#3fb950;margin-right:8px}
.ai-msg.assistant .role{font-family:'JetBrains Mono',monospace;font-size:10px;color:#58a6ff;margin-bottom:6px}
.ai-terminal{background:#010409;border:1px solid #30363d;border-radius:8px;padding:10px;font-family:'JetBrains Mono',monospace;font-size:11px;max-height:160px;overflow-y:auto}
.ai-terminal .line{display:flex;gap:8px;margin-bottom:4px}
.ai-terminal .prompt{color:#3fb950;flex-shrink:0}
.ai-terminal .cmd{color:#79c0ff}
.ai-terminal .ok{color:#3fb950}
.ai-terminal .err{color:#f85149}
.ai-result-code{background:#010409;border:1px solid #30363d;border-radius:8px;padding:12px;font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.5;color:#c9d1d9;max-height:200px;overflow:auto;position:relative}
.ai-result-code::before{content:attr(data-lang);position:absolute;top:6px;right:10px;font-size:9px;color:#484f58;text-transform:uppercase}
.ai-provider-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:20px;
  background:rgba(88,166,255,.1);border:1px solid rgba(88,166,255,.3);font-size:10px;font-family:'JetBrains Mono',monospace;color:#58a6ff}
.ai-send-btn{width:100%;padding:10px;border-radius:8px;border:none;cursor:pointer;font-weight:700;font-size:12px;
  font-family:'JetBrains Mono',monospace;background:linear-gradient(135deg,#238636,#2ea043);color:#fff;margin-top:8px}
.ai-send-btn:hover{filter:brightness(1.1)}
.ai-send-btn:disabled{opacity:.5;cursor:not-allowed}
.ai-overlay{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:199;opacity:0;pointer-events:none;transition:opacity .28s}
.ai-overlay.open{opacity:1;pointer-events:auto}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="bg-glow"></div>
<div id="app"></div>
<div id="notifications"></div>
<div id="context-menu-host"></div>

<script>
// ==================== 상태 ====================
const COLLAB_COLORS = ['#00d4ff','#ff3b8b','#00ff88','#ffaa00','#a855f7'];

const state = {
  user: null, page: 'home', projects: [], project: null,
  files: {}, currentFile: null, openTabs: [],
  editor: null, socket: null, presenceSocket: null, onlineUsers: [],
  chat: [], sidebarOpen: false, fileSearch: '',
  editingStatus: {},      // {file: {user: ts}}
  collaborators: {},      // {user: {file, line, col, selection, colorIdx}}
  collabDecorations: {},  // editor decoration IDs by user
  collabWidgets: {},
  applyingRemoteChange: false,
  savedLines: new Set(),  // 저장된 줄 번호
  modifiedLines: new Set(),
  dirtyTabs: new Set(),
  settings: null,
  friends: [],
  viewedUser: null,
};

const api = {
  async get(url){ const r=await fetch(url); return r.json(); },
  async post(url,data){ const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})}); return r.json(); },
  async del(url,data){ const r=await fetch(url,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})}); return r.json(); },
};

function notify(msg, type='info'){
  const color = {info:'#00d4ff',success:'#00ff88',error:'#ff3b8b',warn:'#ffaa00'}[type];
  const el=document.createElement('div');
  el.className='notification';
  el.style.borderColor=color;
  el.innerHTML=`<i class="fas ${type==='success'?'fa-check-circle':type==='error'?'fa-times-circle':type==='warn'?'fa-exclamation-triangle':'fa-info-circle'}" style="color:${color};margin-right:8px"></i>${msg}`;
  document.getElementById('notifications').appendChild(el);
  setTimeout(()=>{el.style.opacity='0';setTimeout(()=>el.remove(),300)},3000);
}

function getCollabColor(user){
  if (!state.collaborators[user]) {
    const idx = Object.keys(state.collaborators).length % 5;
    state.collaborators[user] = {colorIdx: idx};
  }
  return COLLAB_COLORS[state.collaborators[user].colorIdx || 0];
}

function roleOf(username){
  const member = state.project?.members?.[username];
  return typeof member === 'object' ? (member.role || 'viewer') : (member || 'viewer');
}

function permsOf(username){
  return state.project?.member_permissions?.[username] || {};
}

function setFileSearch(value){
  state.fileSearch = value;
  render();
}

function canDo(permission){
  if (!state.user || !state.project) return false;
  return !!permsOf(state.user.username)[permission];
}

// ==================== 라우팅 ====================
async function init(){
  const me = await api.get('/api/me');
  state.user = me.user;
  if (state.user) state.settings = state.user.settings || {};
  setupPresenceSocket();
  navigate(location.hash.slice(1) || 'home');
  window.addEventListener('hashchange', ()=> navigate(location.hash.slice(1)||'home'));
}

function setupPresenceSocket(){
  if (!state.user || state.presenceSocket) return;
  state.presenceSocket = io();
  state.presenceSocket.on('project_invite', d=>{
    notify(`${d.from}님이 "${d.project_name}" 프로젝트에 초대했습니다`,'success');
  });
}

async function navigate(page){
  const [p, ...args] = page.split('/');
  state.page = p;
  state.sidebarOpen = false;
  
  // 프로젝트 페이지 떠나기
  if (state.socket && p !== 'project') {
    state.socket.emit('leave_project', {pid: state.project?.id, user: state.user?.username});
    state.socket.disconnect();
    state.socket = null;
    state.collaborators = {};
    state.collabWidgets = {};
    state.editingStatus = {};
  }
  
  if (p==='dashboard' || p==='explore' || p==='trending') {
    const sort = p==='trending'?'popular':'recent';
    const r = await api.get(`/api/projects?sort=${sort}`);
    state.projects = r.projects;
  }
  if (p==='project' && args[0]) {
    const r = await api.get(`/api/projects/${args[0]}`);
    state.project = r.project;
    if (state.project) {
      state.files = state.project.files;
      state.chat = state.project.chat || [];
      const firstFile = Object.keys(state.files)[0];
      state.currentFile = firstFile;
      state.openTabs = firstFile ? [firstFile] : [];
      state.dirtyTabs = new Set();
      state.savedLines = new Set();
      state.modifiedLines = new Set();
      setupSocket(args[0]);
    }
  }
  if (p==='profile' && args[0]) {
    const r = await api.get(`/api/users/${args[0]}`);
    state.viewedUser = r.user;
  }
  if (p==='friends') {
    const r = await api.get('/api/friends');
    state.friends = r.friends || [];
  }
  
  render();
  if (p==='project' && state.currentFile) setTimeout(initEditor, 100);
}

function render(){
  const app = document.getElementById('app');
  const pages = {
    'home': renderHome, 'login': renderLogin, 'register': renderRegister,
    'dashboard': renderDashboard, 'explore': renderExplore, 'trending': renderTrending,
    'project': renderProject, 'profile': renderProfile, 'friends': renderFriends, 'settings': renderSettings,
    'create': renderCreate, 'upload': renderUpload, 'tools': renderTools,
  };
  const fn = pages[state.page] || renderHome;
  app.innerHTML = fn();
  attachHandlers();
}

// ==================== 공통 ====================
function topNav(){
  return `
  <nav class="glass-strong sticky top-0 z-40 px-4 md:px-8 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border)">
    <div class="flex items-center gap-4">
      <button class="md:hidden text-xl" onclick="toggleSidebar()"><i class="fas fa-bars"></i></button>
      <a href="#home" class="flex items-center gap-2">
        <div class="mc-block shake-on-hover" style="width:36px;height:36px"></div>
        <span class="text-xl font-extrabold gradient-text hidden sm:inline">MineScript Hub</span>
      </a>
    </div>
    <div class="hidden md:flex items-center gap-2 flex-1 max-w-lg mx-8">
      <div class="relative w-full">
        <i class="fas fa-search absolute left-4 top-1/2 -translate-y-1/2 text-gray-500"></i>
        <input class="input pl-11" placeholder="프로젝트, 태그, 사용자 검색..." onkeyup="if(event.key==='Enter')doSearch(this.value)">
      </div>
    </div>
    <div class="flex items-center gap-3">
      ${state.user ? `
        <a href="#create" class="neon-btn hidden sm:inline-flex"><i class="fas fa-plus"></i>새 프로젝트</a>
        <a href="#upload" class="neon-btn purple hidden md:inline-flex"><i class="fas fa-upload"></i>업로드</a>
        <div class="relative cursor-pointer" onclick="location.hash='profile/${state.user.username}'">
          <div class="w-9 h-9 rounded-full flex items-center justify-center text-lg" 
               style="background:linear-gradient(135deg,#00d4ff,#a855f7)">${state.user.avatar||'👤'}</div>
        </div>
      ` : `
        <a href="#login" class="neon-btn">로그인</a>
        <a href="#register" class="mc-btn">시작하기</a>
      `}
    </div>
  </nav>`;
}

function sidebar(active){
  if (!state.user) return '';
  const items = [
    {id:'dashboard', icon:'fa-th-large', label:'대시보드'},
    {id:'explore', icon:'fa-compass', label:'탐색'},
    {id:'trending', icon:'fa-fire', label:'트렌딩'},
    {id:'create', icon:'fa-plus-square', label:'새 프로젝트'},
    {id:'upload', icon:'fa-upload', label:'팩 업로드'},
    {id:'tools', icon:'fa-toolbox', label:'생성기 도구'},
    {id:'friends', icon:'fa-user-friends', label:'친구'},
    {id:'profile/'+state.user.username, icon:'fa-user', label:'내 프로필', match:'profile'},
    {id:'settings', icon:'fa-cog', label:'설정'},
  ];
  return `
  <aside id="sidebar" class="glass-strong w-64 flex-shrink-0 p-3 flex flex-col gap-1" 
         style="border-right:1px solid var(--border);min-height:calc(100vh - 64px)">
    <div class="px-3 py-2 text-xs font-bold text-gray-500 uppercase tracking-wider">메인</div>
    ${items.map(i=>`
      <a href="#${i.id}" class="sidebar-item ${(i.match||i.id)===active?'active':''}">
        <i class="fas ${i.icon} w-5"></i><span>${i.label}</span>
      </a>
    `).join('')}
    <div class="mt-auto p-3 glass rounded-xl">
      <div class="text-xs text-gray-400 mb-2">⚡ Quick Stats</div>
      <div class="flex justify-between text-sm"><span>프로젝트</span><span class="text-cyan-400 font-bold">${state.projects.length||0}</span></div>
    </div>
  </aside>`;
}

function toggleSidebar(){
  const sb = document.getElementById('sidebar');
  if (sb) sb.classList.toggle('open');
}

function doSearch(q){
  location.hash = 'explore';
  setTimeout(()=>{
    const input = document.querySelector('#search-explore');
    if(input){ input.value = q; filterProjects(q); }
  }, 100);
}

// ==================== 홈 ====================
function renderHome(){
  return `
  ${topNav()}
  <div class="px-4 md:px-8 py-12 md:py-20 max-w-7xl mx-auto fade-in">
    <div class="text-center mb-16">
      <div class="inline-block mb-6 px-4 py-2 rounded-full glass text-sm">
        <span class="text-cyan-400">⚡</span> Minecraft Bedrock Script API 공동 개발 플랫폼
      </div>
      <h1 class="hero-text mb-6">
        <span class="gradient-text glow-text">실시간 협업으로</span><br>
        Minecraft 애드온을 만드세요
      </h1>
      <p class="text-lg text-gray-400 max-w-2xl mx-auto mb-10">
        VSCode 수준의 에디터, 실시간 공동 편집, 코드 작성 도우미까지.<br>
        팀과 함께 Behavior Pack과 Resource Pack을 개발하세요.
      </p>
      <div class="flex gap-4 justify-center flex-wrap">
        <a href="#${state.user?'dashboard':'register'}" class="mc-btn">🚀 지금 시작하기</a>
        <a href="#explore" class="neon-btn purple">📦 프로젝트 둘러보기</a>
      </div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-20">
      ${[
        {n:'12,438', l:'활성 프로젝트'},{n:'3,892', l:'개발자'},
        {n:'89,217', l:'다운로드'},{n:'24/7', l:'실시간 협업'},
      ].map(s=>`
        <div class="card p-6 text-center">
          <div class="text-3xl font-extrabold gradient-text mb-1">${s.n}</div>
          <div class="text-sm text-gray-400">${s.l}</div>
        </div>
      `).join('')}
    </div>
    <div class="mb-20">
      <h2 class="text-3xl font-bold text-center mb-12">🔥 핵심 기능</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        ${[
          {i:'fa-code',t:'Monaco 에디터',d:'VSCode와 동일한 코드 편집기. 자동완성, 미니맵, 코드 폴딩 지원.'},
          {i:'fa-users',t:'실시간 협업',d:'다른 사람의 커서, 선택영역, 편집 중인 파일이 실시간으로 보입니다.'},
          {i:'fa-wand-magic-sparkles',t:'코드 작성 도우미',d:'반복적인 Script API 코드를 빠르게 작성합니다.'},
          {i:'fa-upload',t:'팩 업로드',d:'기존 .mcpack 파일을 업로드하여 협업을 시작하세요.'},
          {i:'fa-magic',t:'스마트 자동완성',d:'Bedrock Script API 100+ 스니펫 내장.'},
          {i:'fa-download',t:'원클릭 빌드',d:'.mcpack 형식으로 즉시 export.'},
        ].map(f=>`
          <div class="card p-6">
            <div class="w-12 h-12 rounded-lg flex items-center justify-center mb-4" 
                 style="background:linear-gradient(135deg,rgba(0,212,255,.2),rgba(168,85,247,.2));border:1px solid var(--border)">
              <i class="fas ${f.i} text-xl text-cyan-400"></i>
            </div>
            <h3 class="text-lg font-bold mb-2">${f.t}</h3>
            <p class="text-sm text-gray-400">${f.d}</p>
          </div>
        `).join('')}
      </div>
    </div>
  </div>`;
}

// ==================== 인증 ====================
function renderLogin(){
  return `
  ${topNav()}
  <div class="flex items-center justify-center min-h-[calc(100vh-72px)] p-4 fade-in">
    <div class="glass-strong rounded-2xl p-8 w-full max-w-md">
      <div class="text-center mb-8">
        <div class="mc-block mx-auto mb-4" style="width:60px;height:60px"></div>
        <h2 class="text-2xl font-bold">다시 오신걸 환영합니다</h2>
      </div>
      <form onsubmit="doLogin(event)" class="space-y-4">
        <input id="login-user" class="input" placeholder="아이디" required>
        <input id="login-pw" type="password" class="input" placeholder="비밀번호" required>
        <button class="mc-btn w-full">로그인</button>
        <div class="text-center text-xs text-gray-500">데모: <code class="text-cyan-400">demo / demo</code></div>
        <div class="text-center text-sm text-gray-400">계정 없으신가요? <a href="#register" class="text-cyan-400">가입하기</a></div>
      </form>
    </div>
  </div>`;
}
async function doLogin(e){
  e.preventDefault();
  const r = await api.post('/api/login', {
    username: document.getElementById('login-user').value,
    password: document.getElementById('login-pw').value,
  });
  if (r.ok){ notify('환영합니다!','success'); location.hash='dashboard'; init(); }
  else notify(r.error,'error');
}
function renderRegister(){
  return `
  ${topNav()}
  <div class="flex items-center justify-center min-h-[calc(100vh-72px)] p-4 fade-in">
    <div class="glass-strong rounded-2xl p-8 w-full max-w-md">
      <div class="text-center mb-8">
        <div class="mc-block mx-auto mb-4" style="width:60px;height:60px"></div>
        <h2 class="text-2xl font-bold gradient-text">가입</h2>
      </div>
      <form onsubmit="doRegister(event)" class="space-y-4">
        <input id="reg-user" class="input" placeholder="아이디" required minlength="3">
        <input id="reg-email" type="email" class="input" placeholder="이메일">
        <input id="reg-pw" type="password" class="input" placeholder="비밀번호" required minlength="3">
        <input id="reg-avatar" class="input" placeholder="아바타 이모지" value="🧑‍💻">
        <button class="mc-btn w-full">가입하기</button>
        <div class="text-center text-sm text-gray-400">이미 계정이? <a href="#login" class="text-cyan-400">로그인</a></div>
      </form>
    </div>
  </div>`;
}
async function doRegister(e){
  e.preventDefault();
  const r = await api.post('/api/register', {
    username: document.getElementById('reg-user').value,
    email: document.getElementById('reg-email').value,
    password: document.getElementById('reg-pw').value,
    avatar: document.getElementById('reg-avatar').value,
  });
  if (r.ok){ notify('가입 완료!','success'); location.hash='dashboard'; init(); }
  else notify(r.error,'error');
}

// ==================== 대시보드 ====================
function renderDashboard(){
  if (!state.user) { location.hash='login'; return ''; }
  return `
  ${topNav()}
  <div class="flex">${sidebar('dashboard')}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <div class="flex items-center justify-between mb-8 flex-wrap gap-4">
        <div>
          <h1 class="text-3xl font-bold">대시보드</h1>
          <p class="text-gray-400 mt-1">안녕하세요, ${state.user.username}님 👋</p>
        </div>
        <div class="flex gap-2">
          <a href="#upload" class="neon-btn purple"><i class="fas fa-upload"></i> 팩 업로드</a>
          <a href="#create" class="mc-btn">+ 새 프로젝트</a>
        </div>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        ${[
          {n:state.projects.length,l:'프로젝트',i:'fa-folder',c:'cyan'},
          {n:state.projects.reduce((s,p)=>s+p.likes,0),l:'좋아요',i:'fa-heart',c:'pink'},
          {n:state.projects.reduce((s,p)=>s+p.downloads,0),l:'다운로드',i:'fa-download',c:'green'},
          {n:state.user.followers.length,l:'팔로워',i:'fa-users',c:'purple'},
        ].map(s=>`
          <div class="card p-5">
            <i class="fas ${s.i} text-2xl text-${s.c}-400"></i>
            <div class="text-2xl font-bold mt-2">${s.n}</div>
            <div class="text-sm text-gray-400">${s.l}</div>
          </div>
        `).join('')}
      </div>
      <h2 class="text-xl font-bold mb-4">최근 프로젝트</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        ${state.projects.map(projectCard).join('') || '<div class="text-gray-500">프로젝트가 없습니다</div>'}
      </div>
    </main>
  </div>`;
}

function projectCard(p){
  return `
    <a href="#project/${p.id}" class="card p-5 block fade-in">
      <div class="flex items-start gap-3 mb-3">
        <div class="text-4xl">${p.thumbnail||'📦'}</div>
        <div class="flex-1 min-w-0">
          <div class="font-bold text-lg truncate">${p.name}</div>
          <div class="text-xs text-gray-500">@${p.owner}</div>
        </div>
      </div>
      <p class="text-sm text-gray-400 mb-3 line-clamp-2" style="min-height:40px">${p.description||'설명 없음'}</p>
      <div class="mb-3">${(p.tags||[]).slice(0,3).map(t=>`<span class="tag">${t}</span>`).join('')}</div>
      <div class="flex items-center justify-between text-xs text-gray-400 pt-3" style="border-top:1px solid var(--border)">
        <span><i class="fas fa-heart text-pink-400"></i> ${p.likes}</span>
        <span><i class="fas fa-download text-cyan-400"></i> ${p.downloads}</span>
        <span><i class="fas fa-clock"></i> ${timeAgo(p.created_at)}</span>
      </div>
    </a>`;
}
function timeAgo(ts){
  const s = Date.now()/1000 - ts;
  if (s<60) return '방금'; if (s<3600) return Math.floor(s/60)+'분 전';
  if (s<86400) return Math.floor(s/3600)+'시간 전'; return Math.floor(s/86400)+'일 전';
}

// ==================== 탐색 ====================
function renderExplore(){ return renderListing('explore','🌍 프로젝트 탐색','모든 공개 프로젝트'); }
function renderTrending(){ return renderListing('trending','🔥 트렌딩','인기 프로젝트'); }
function renderListing(active, title, desc){
  return `
  ${topNav()}
  <div class="flex">${sidebar(active)}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <h1 class="text-3xl font-bold mb-2">${title}</h1>
      <p class="text-gray-400 mb-6">${desc}</p>
      <div class="mb-6 flex gap-3 flex-wrap">
        <input id="search-explore" class="input flex-1 min-w-[200px]" placeholder="🔎 검색" onkeyup="filterProjects(this.value)">
        <select class="input" style="max-width:200px" onchange="sortProjects(this.value)">
          <option value="recent">최신순</option>
          <option value="popular" ${active==='trending'?'selected':''}>인기순</option>
          <option value="downloads">다운로드순</option>
        </select>
      </div>
      <div id="project-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        ${state.projects.map(projectCard).join('')}
      </div>
    </main>
  </div>`;
}
async function filterProjects(q){
  const r = await api.get('/api/projects?q='+encodeURIComponent(q));
  state.projects = r.projects;
  document.getElementById('project-grid').innerHTML = state.projects.map(projectCard).join('');
}
async function sortProjects(sort){
  const r = await api.get('/api/projects?sort='+sort);
  state.projects = r.projects;
  document.getElementById('project-grid').innerHTML = state.projects.map(projectCard).join('');
}

// ==================== 새 프로젝트 ====================
function renderCreate(){
  if (!state.user) { location.hash='login'; return ''; }
  return `
  ${topNav()}
  <div class="flex">${sidebar('create')}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <h1 class="text-3xl font-bold mb-2">✨ 새 프로젝트</h1>
      <p class="text-gray-400 mb-8">템플릿을 선택하고 시작하세요</p>
      <form onsubmit="doCreate(event)" class="max-w-3xl space-y-6">
        <div class="card p-6 space-y-4">
          <div><label class="block text-sm font-semibold mb-2">프로젝트 이름</label>
            <input id="c-name" class="input" required></div>
          <div><label class="block text-sm font-semibold mb-2">설명</label>
            <textarea id="c-desc" class="input" rows="3"></textarea></div>
          <div><label class="block text-sm font-semibold mb-2">썸네일 이모지</label>
            <input id="c-thumb" class="input" value="📦"></div>
          <div><label class="block text-sm font-semibold mb-2">태그 (쉼표)</label>
            <input id="c-tags" class="input" placeholder="weapon, magic"></div>
        </div>
        <div class="card p-6">
          <label class="block text-sm font-semibold mb-3">템플릿</label>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3" id="template-grid">
            ${[
              {v:'behavior',i:'⚙️',t:'Behavior Pack',d:'Script API'},
              {v:'resource',i:'🎨',t:'Resource Pack',d:'텍스처/모델'},
              {v:'both',i:'📦',t:'두 팩',d:'완전한 애드온'},
              {v:'empty',i:'📄',t:'빈 프로젝트',d:'처음부터'},
            ].map((t,i)=>`
              <label class="card p-4 cursor-pointer text-center hover:border-cyan-400" style="border-radius:10px">
                <input type="radio" name="template" value="${t.v}" ${i===0?'checked':''} class="hidden">
                <div class="text-4xl mb-2">${t.i}</div>
                <div class="font-bold">${t.t}</div>
                <div class="text-xs text-gray-400">${t.d}</div>
              </label>
            `).join('')}
          </div>
        </div>
        <div class="card p-6">
          <label class="flex items-center gap-3 cursor-pointer">
            <label class="switch"><input id="c-public" type="checkbox" checked><span class="slider"></span></label>
            <div><div class="font-semibold">🌍 공개</div>
              <div class="text-xs text-gray-400">모든 사용자가 볼 수 있음</div></div>
          </label>
        </div>
        <div class="flex gap-3">
          <button class="mc-btn">🚀 생성</button>
          <a href="#dashboard" class="neon-btn">취소</a>
        </div>
      </form>
    </main>
  </div>`;
}
async function doCreate(e){
  e.preventDefault();
  const r = await api.post('/api/projects', {
    name: document.getElementById('c-name').value,
    description: document.getElementById('c-desc').value,
    thumbnail: document.getElementById('c-thumb').value,
    tags: document.getElementById('c-tags').value.split(',').map(s=>s.trim()).filter(Boolean),
    public: document.getElementById('c-public').checked,
    template: document.querySelector('input[name=template]:checked').value,
  });
  if (r.ok){ notify('생성됨!','success'); location.hash='project/'+r.id; }
  else notify(r.error,'error');
}

// ==================== 팩 업로드 ====================
function renderUpload(){
  if (!state.user) { location.hash='login'; return ''; }
  return `
  ${topNav()}
  <div class="flex">${sidebar('upload')}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <h1 class="text-3xl font-bold mb-2">📤 팩 업로드</h1>
      <p class="text-gray-400 mb-8">.mcpack 또는 .zip 파일을 업로드하여 프로젝트를 만드세요</p>
      <form id="upload-form" class="max-w-2xl space-y-6" onsubmit="doUpload(event)">
        <div class="card p-8" id="drop-zone">
          <div class="text-center py-8 cursor-pointer" onclick="document.getElementById('pack-file').click()">
            <i class="fas fa-cloud-upload-alt text-6xl text-cyan-400 mb-4"></i>
            <div class="text-xl font-bold mb-2">파일을 끌어다 놓거나 클릭하세요</div>
            <div class="text-sm text-gray-400">.mcpack, .mcaddon, .zip 지원 (최대 50MB)</div>
            <input id="pack-file" type="file" accept=".mcpack,.mcaddon,.zip" class="hidden" onchange="onFileSelected()">
            <div id="file-info" class="mt-4 text-sm text-green-400 hidden"></div>
          </div>
        </div>
        <div class="card p-6 space-y-4">
          <div><label class="block text-sm font-semibold mb-2">프로젝트 이름</label>
            <input id="up-name" class="input" required></div>
          <div><label class="block text-sm font-semibold mb-2">설명</label>
            <textarea id="up-desc" class="input" rows="3"></textarea></div>
          <label class="flex items-center gap-3">
            <label class="switch"><input id="up-public" type="checkbox" checked><span class="slider"></span></label>
            <span>🌍 공개</span>
          </label>
        </div>
        <div class="flex gap-3">
          <button class="mc-btn">📤 업로드</button>
          <a href="#dashboard" class="neon-btn">취소</a>
        </div>
      </form>
    </main>
  </div>`;
}

function onFileSelected(){
  const f = document.getElementById('pack-file').files[0];
  if (!f) return;
  const info = document.getElementById('file-info');
  info.classList.remove('hidden');
  info.innerHTML = `<i class="fas fa-check-circle"></i> ${f.name} (${(f.size/1024).toFixed(1)} KB)`;
  if (!document.getElementById('up-name').value) {
    document.getElementById('up-name').value = f.name.replace(/\.(mcpack|mcaddon|zip)$/i,'');
  }
}

async function doUpload(e){
  e.preventDefault();
  const f = document.getElementById('pack-file').files[0];
  if (!f) return notify('파일을 선택하세요','error');
  
  const fd = new FormData();
  fd.append('pack', f);
  fd.append('name', document.getElementById('up-name').value);
  fd.append('description', document.getElementById('up-desc').value);
  fd.append('public', document.getElementById('up-public').checked);
  
  notify('업로드 중...','info');
  const res = await fetch('/api/projects/upload', {method:'POST', body:fd});
  const r = await res.json();
  if (r.ok){ notify(`업로드 완료! ${r.files}개 파일`,'success'); location.hash='project/'+r.id; }
  else notify(r.error||'업로드 실패','error');
}

// ==================== 프로젝트 (메인 에디터) ====================
function renderProject(){
  if (!state.project) return `${topNav()}<div class="p-8 text-center text-gray-400">프로젝트를 찾을 수 없습니다</div>`;
  const p = state.project;
  const role = state.user ? roleOf(state.user.username) : 'viewer';
  const liked = state.user && p.likes.includes(state.user.username);
  
  return `
  ${topNav()}
  <div class="flex" style="height:calc(100vh - 64px)">
    <!-- 파일 트리 -->
    <aside class="glass w-60 flex-shrink-0 flex flex-col" style="border-right:1px solid var(--border)">
      <div class="p-3" style="border-bottom:1px solid var(--border)">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-2xl">${p.thumbnail}</span>
          <div class="flex-1 min-w-0">
            <div class="font-bold truncate text-sm">${p.name}</div>
            <div class="text-xs text-gray-500">@${p.owner}</div>
          </div>
        </div>
        <span class="role-${role}">${role}</span>
      </div>
      <div class="p-3 border-b border-gray-800">
        <div class="flex items-center justify-between mb-2">
          <div>
            <div class="text-xs uppercase tracking-[0.2em] text-gray-500">Explorer</div>
            <div class="text-sm text-white font-semibold">프로젝트 파일</div>
          </div>
          ${canDo('manage_files')?`<button class="text-cyan-400 hover:text-white text-sm" onclick="newFile()"><i class="fas fa-file-plus"></i></button>`:''}
        </div>
        <input id="file-search" class="input" placeholder="파일 검색..." value="${state.fileSearch||''}" oninput="setFileSearch(this.value)">
        <div class="mt-2 flex items-center justify-between text-[11px] text-gray-500">
          <span>${Object.keys(state.files).length}개 파일</span>
          <span>${state.openTabs.length} 열림</span>
        </div>
        ${canDo('manage_files')?`<div class="mt-3 flex gap-2 text-xs text-gray-400"><button class="hover:text-white" onclick="newFolder()"><i class="fas fa-folder-plus"></i> 새 폴더</button></div>`:''}
      </div>
      <div class="flex-1 overflow-auto scrollbar px-2 py-2" id="file-tree">
        ${renderFileTree()}
      </div>
      <div id="project-members" class="p-3 scrollbar" style="border-top:1px solid var(--border);max-height:200px;overflow:auto">
        ${renderProjectMembers(role, p)}
      </div>
    </aside>
    
    <!-- 에디터 -->
    <div class="flex-1 flex flex-col min-w-0">
      <div class="flex overflow-x-auto scrollbar" style="background:#0a0e1a;border-bottom:1px solid var(--border)">
        ${state.openTabs.map(f=>{
          const dirty = state.dirtyTabs.has(f);
          const editingUsers = Object.keys(state.editingStatus[f]||{}).filter(u=>u!==state.user?.username);
          return `
          <div class="tab ${f===state.currentFile?'active':''}" onclick="switchTab('${f}')">
            <i class="fas ${fileIcon(f)} text-xs"></i>
            <span>${f.split('/').pop()}</span>
            ${editingUsers.length?`<span class="editing-indicator"><i class="fas fa-pen" style="font-size:8px"></i> ${editingUsers[0]}</span>`:''}
            ${dirty?`<span class="dirty"></span>`:''}
            <i class="fas fa-times text-gray-500 hover:text-white ml-2" onclick="event.stopPropagation();closeTab('${f}')"></i>
          </div>
        `;}).join('')}
        <div class="ml-auto flex items-center gap-2 px-3">
          ${canDo('invite_members')?`<button class="text-xs text-gray-400 hover:text-cyan-400" onclick="openInviteModal()"><i class="fas fa-user-plus"></i> 초대</button>`:''}
          ${canDo('manage_roles')?`<button class="text-xs text-gray-400 hover:text-cyan-400" onclick="openRoleSettings()"><i class="fas fa-users-cog"></i> 권한</button>`:''}
          ${canDo('bedrock_sync')?`<button class="text-xs text-gray-400 hover:text-green-400" onclick="openBedrockLive()"><i class="fas fa-plug"></i> Bedrock Live</button>`:''}
          <button class="text-xs text-gray-400 hover:text-cyan-400" onclick="runBuild()"><i class="fas fa-hammer"></i> 빌드</button>
          <button class="text-xs text-gray-400 hover:text-cyan-400" onclick="openAIPanel()"><i class="fas fa-robot"></i> CoBuddy</button>
          <button class="text-xs text-gray-400 hover:text-cyan-400" onclick="exportProject()"><i class="fas fa-download"></i> Export</button>
          <button class="text-xs ${liked?'text-pink-400':'text-gray-400'} hover:text-pink-400" onclick="likeProject()"><i class="fas fa-heart"></i> ${p.likes.length}</button>
        </div>
      </div>
      <div id="editor" style="flex:1;min-height:0"></div>
      <div class="px-4 py-1 text-xs flex items-center justify-between" style="background:#0a0e1a;border-top:1px solid var(--border)">
        <div class="flex gap-4 text-gray-400" id="editor-status">
          <span><i class="fas fa-code-branch"></i> main</span>
          <span id="save-status" class="text-green-400">● 저장됨</span>
          <span id="project-online-count"><i class="fas fa-users text-cyan-400"></i> ${state.onlineUsers.length}명</span>
          <span id="cursor-pos">Ln 1, Col 1</span>
        </div>
        <div class="flex gap-4 text-gray-400">
          <span>${state.currentFile?fileLang(state.currentFile).toUpperCase():''}</span>
          <span>UTF-8</span>
        </div>
      </div>
    </div>
    
    <!-- 채팅 -->
    <aside class="glass w-72 flex-shrink-0 hidden lg:flex flex-col" style="border-left:1px solid var(--border)">
      <div class="p-3 flex items-center justify-between" style="border-bottom:1px solid var(--border)">
        <span class="font-bold text-sm"><i class="fas fa-comments text-cyan-400"></i> 채팅</span>
        <span id="chat-online-count" class="text-xs text-gray-500">${state.onlineUsers.length} 온라인</span>
      </div>
      <div class="p-3" style="border-bottom:1px solid var(--border)">
        <div class="text-xs text-gray-500 mb-2">현재 접속</div>
        <div id="project-online-users" class="flex flex-wrap gap-2">
          ${renderOnlineUsers()}
        </div>
      </div>
      <div id="chat-list" class="flex-1 overflow-auto scrollbar p-2">
        ${renderChat()}
      </div>
      <div class="p-2" style="border-top:1px solid var(--border)">
        <input id="chat-input" class="input" placeholder="메시지 (Enter)" onkeydown="if(event.key==='Enter')sendChat()">
      </div>
    </aside>
  </div>`;
}

function renderProjectMembers(role, p){
  return `
    <div class="text-xs text-gray-500 mb-2">멤버 (${Object.keys(p.members).length})</div>
    ${Object.entries(p.members).map(([u,member])=>{
      const r = roleOf(u);
      const isOnline = state.onlineUsers.find(o=>o.user===u);
      const editing = isOnline?.file;
      const editingStatus = state.editingStatus?.[editing] || {};
      const isEditing = u in editingStatus;
      const canManage = canDo('manage_roles') && u !== p.owner;
      const collabColor = getCollabColor(u);
      return `
      <div class="flex items-center gap-2 text-sm py-1 px-2 rounded hover:bg-white/5" style="transition:all .2s">
        <span class="online-dot ${isOnline?'':'opacity-30'}" style="${isOnline?'background:'+collabColor+';box-shadow:0 0 8px '+collabColor:''}"></span>
        <div class="flex-1 min-w-0">
          <div class="truncate ${isEditing?'font-semibold':''}"><span class="text-xs">${u}</span></div>
          ${editing?`<div class="text-xs ${isEditing?'text-yellow-400 font-bold':'text-orange-400'} truncate flex items-center gap-1">
            ${isEditing?'<span class="editing-indicator"><i class="fas fa-pencil text-xs"></i> 수정 중</span>':'수정: '}${editing.split('/').pop()}${isOnline.editingLine?' L'+isOnline.editingLine:''}
          </div>`:''}
        </div>
        ${canManage?`
          <select class="input" style="width:100px;padding:5px 20px 5px 8px;font-size:11px" onchange="changeMemberRole('${u}', this.value)">
            ${['admin','developer','viewer'].map(x=>`<option value="${x}" ${x===r?'selected':''}>${x}</option>`).join('')}
          </select>
        `:`<span class="role-${r}" style="font-size:9px">${r}</span>`}
      </div>
    `;}).join('')}
  `;
}

function renderOnlineUsers(){
  return state.onlineUsers.map(o=>`
    <span class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded" style="background:rgba(255,255,255,.06)">
      <span class="online-dot" style="background:${getCollabColor(o.user)};box-shadow:0 0 8px ${getCollabColor(o.user)}"></span>
      ${o.user}
    </span>
  `).join('') || '<span class="text-xs text-gray-500">없음</span>';
}

function refreshPresenceUI(){
  if (!state.project) return;
  const role = (state.user && state.project.members[state.user.username]) || 'viewer';
  const members = document.getElementById('project-members');
  if (members) members.innerHTML = renderProjectMembers(role, state.project);
  const online = document.getElementById('project-online-users');
  if (online) online.innerHTML = renderOnlineUsers();
  const projectCount = document.getElementById('project-online-count');
  if (projectCount) projectCount.innerHTML = `<i class="fas fa-users text-cyan-400"></i> ${state.onlineUsers.length}명`;
  const chatCount = document.getElementById('chat-online-count');
  if (chatCount) chatCount.textContent = `${state.onlineUsers.length} 온라인`;
}

async function openInviteModal(){
  const r = await api.get('/api/friends');
  const friends = r.friends || [];
  const myRole = roleOf(state.user?.username);
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-lg w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold gradient-text"><i class="fas fa-user-plus"></i> 친구 초대</h3>
          <button class="text-gray-400 hover:text-white" onclick="closeModal()"><i class="fas fa-times"></i></button>
        </div>
        <div class="space-y-2 max-h-80 overflow-auto scrollbar">
          ${friends.map(f=>{
            const already = roleOf(f.username);
            const isMember = !!state.project.members[f.username];
            return `
            <div class="card p-3 flex items-center gap-3">
              <span class="online-dot ${f.online?'':'opacity-30'}" style="${f.online?'background:#00ff88;box-shadow:0 0 8px #00ff88':''}"></span>
              <div class="text-2xl">${f.avatar||'👤'}</div>
              <div class="flex-1 min-w-0">
                <div class="font-semibold truncate">${f.username}</div>
                <div class="text-xs ${f.online?'text-green-400':'text-gray-500'}">${f.online?'온라인':'오프라인'}${f.mutual?' · 맞친':''}</div>
              </div>
              ${isMember?`<span class="role-${already}">${already}</span>`:`
                <select id="invite-role-${f.username}" class="input" style="width:120px;padding:8px">
                  ${myRole==='admin'?'<option value="admin">admin</option>':''}
                  <option value="developer">developer</option>
                  <option value="viewer">viewer</option>
                </select>
                <button class="mc-btn" onclick="inviteFriend('${f.username}')">초대</button>
              `}
            </div>`;
          }).join('') || '<div class="text-center text-gray-500 py-8">아직 친구가 없습니다</div>'}
        </div>
        <div class="mt-4 flex gap-2">
          <input id="quick-friend-name" class="input flex-1" placeholder="아이디로 친구 추가">
          <button class="neon-btn" onclick="quickAddFriend()">친추</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
}

async function inviteFriend(username){
  const role = document.getElementById(`invite-role-${username}`)?.value || 'developer';
  const r = await api.post(`/api/projects/${state.project.id}/invite`, {username, role});
  if (r.ok) {
    state.project.members[username] = role;
    notify(`${username}님 초대 완료`,'success');
    closeModal();
    render(); setTimeout(initEditor,50);
  } else notify(r.error || '초대 실패','error');
}

async function quickAddFriend(){
  const username = document.getElementById('quick-friend-name').value.trim();
  if (!username) return;
  const r = await api.post('/api/friends/add', {username});
  if (r.ok) { notify('친구 추가 완료','success'); closeModal(); openInviteModal(); }
  else notify(r.error || '친구 추가 실패','error');
}

async function changeMemberRole(username, role){
  const permissions = {};
  document.querySelectorAll(`[data-perm-user="${username}"]`).forEach(el=>permissions[el.dataset.perm] = el.checked);
  const body = Object.keys(permissions).length ? {role, permissions} : {role};
  const r = await api.post(`/api/projects/${state.project.id}/members/${username}`, body);
  if (r.ok) {
    state.project.members[username] = role;
    state.project.member_permissions = state.project.member_permissions || {};
    state.project.member_permissions[username] = r.permissions || permissions;
    notify(`${username} 권한: ${role}`,'success');
    render(); if (state.currentFile) setTimeout(initEditor,50);
  } else {
    notify(r.error || '권한 변경 실패','error');
    render(); if (state.currentFile) setTimeout(initEditor,50);
  }
}

function openRoleSettings(){
  const members = Object.entries(state.project.members);
  const labels = {
    edit_code:'코드 수정', manage_files:'파일 관리', invite_members:'초대',
    manage_roles:'권한 관리', bedrock_sync:'마크 연동', chat:'채팅',
  };
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-lg w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold gradient-text"><i class="fas fa-users-cog"></i> 멤버 권한</h3>
          <button class="text-gray-400 hover:text-white" onclick="closeModal()"><i class="fas fa-times"></i></button>
        </div>
        <div class="space-y-2">
          ${members.map(([username, member])=>{
            const role = roleOf(username);
            const perms = permsOf(username);
            return `
            <div class="card p-3">
              <div class="flex items-center gap-3">
              <div class="flex-1 min-w-0">
                <div class="font-semibold truncate">${username}</div>
                <div class="text-xs text-gray-500">${username===state.project.owner?'owner':'member'}</div>
              </div>
              ${username===state.project.owner?`<span class="role-admin">admin</span>`:`
                <select id="role-select-${username}" class="input" style="width:140px;padding:8px 28px 8px 10px" onchange="changeMemberRole('${username}', this.value)">
                  ${['admin','developer','viewer'].map(x=>`<option value="${x}" ${x===role?'selected':''}>${x}</option>`).join('')}
                </select>
              `}
              </div>
              <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-3">
                ${Object.entries(labels).map(([key,label])=>`
                  <label class="text-xs flex items-center gap-2 ${username===state.project.owner?'opacity-60':''}">
                    <input type="checkbox" data-perm-user="${username}" data-perm="${key}" ${perms[key]?'checked':''} ${username===state.project.owner?'disabled':''}
                           onchange="changeMemberRole('${username}', document.getElementById('role-select-${username}')?.value || '${role}')">
                    <span>${label}</span>
                  </label>
                `).join('')}
              </div>
            </div>
          `}).join('')}
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
}

async function openBedrockLive(){
  const r = await api.get(`/api/projects/${state.project.id}/bridge`);
  if (!r.ok) return notify(r.error || '브릿지 준비 실패','error');
  const defaultDir = `%LOCALAPPDATA%\\Packages\\Microsoft.MinecraftUWP_8wekyb3d8bbwe\\LocalState\\games\\com.mojang\\development_behavior_packs\\${state.project.name.replace(/[<>:"/\\\\|?*]/g,'_')}`;
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-2xl w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold"><i class="fas fa-plug text-green-400"></i> Bedrock 연동</h3>
          <button class="text-gray-400 hover:text-white" onclick="closeModal()"><i class="fas fa-times"></i></button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
          <div class="card p-3"><div class="text-xs text-gray-500">파일</div><div class="font-bold text-green-400">${r.files}</div></div>
          <div class="card p-3"><div class="text-xs text-gray-500">서버</div><div class="font-bold text-cyan-400 truncate">${r.server}</div></div>
          <div class="card p-3"><div class="text-xs text-gray-500">상태</div><div id="bridge-status-label" class="font-bold text-orange-400">브릿지 대기</div></div>
        </div>
        <div class="space-y-3">
          <button class="mc-btn inline-flex" onclick="downloadBridgeKit()"><i class="fas fa-download"></i> 연동 키트 다운로드</button>
          <div class="card p-4">
            <div class="font-bold text-green-400 mb-2">실행 순서</div>
            <div class="text-sm text-gray-300 leading-7">
              1. ZIP 압축 풀기<br>
              2. start.bat 더블클릭<br>
              3. 웹에서 코드를 수정하면 Bedrock 개발팩 폴더에 자동 반영
            </div>
          </div>
          <div>
            <label class="text-sm text-gray-400 block mb-1">동기화할 팩 폴더</label>
            <input id="bridge-pack-dir" class="input font-mono text-xs" value="${escapeHtml(defaultDir)}">
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label class="text-sm text-gray-400 block mb-1">팩 종류</label>
              <select id="bridge-pack-type" class="input">
                <option value="behavior">Behavior Pack</option>
                <option value="resource">Resource Pack</option>
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-400 block mb-1">동기화 간격</label>
              <select id="bridge-interval" class="input">
                <option value="1">1초</option>
                <option value="2">2초</option>
                <option value="5">5초</option>
                <option value="10">10초</option>
              </select>
            </div>
            <label class="text-sm text-gray-300 flex items-center gap-2 mt-6">
              <input id="bridge-delete-removed" type="checkbox" checked>
              삭제도 반영
            </label>
          </div>
          <div class="text-sm text-gray-400 leading-6">
            Windows 기본 PowerShell로 실행됩니다. 폴더를 직접 바꾸고 다운로드하면 그 위치로 동기화됩니다. Bedrock 적용은 월드에서 /reload 또는 월드 재입장이 가장 안정적입니다.
          </div>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
  startBridgeStatusPoll();
}

function downloadBridgeKit(){
  const dir = document.getElementById('bridge-pack-dir')?.value?.trim() || '';
  const type = document.getElementById('bridge-pack-type')?.value || 'behavior';
  const interval = document.getElementById('bridge-interval')?.value || '1';
  const del = document.getElementById('bridge-delete-removed')?.checked ? 'true' : 'false';
  const url = `/api/projects/${state.project.id}/bridge/kit.zip?pack_dir=${encodeURIComponent(dir)}&pack_type=${encodeURIComponent(type)}&interval=${encodeURIComponent(interval)}&delete_removed=${del}`;
  location.href = url;
}

function startBridgeStatusPoll(){
  const pid = state.project?.id;
  if (!pid) return;
  if (window._bridgeStatusTimer) clearInterval(window._bridgeStatusTimer);
  const update = async ()=>{
    const label = document.getElementById('bridge-status-label');
    if (!label) { clearInterval(window._bridgeStatusTimer); return; }
    const r = await api.get(`/api/projects/${pid}/bridge/status`);
    if (!r.ok) return;
    if (r.online) {
      label.textContent = `연결됨 · ${r.files || 0} files`;
      label.className = 'font-bold text-green-400';
    } else if (r.time) {
      label.textContent = '연결 끊김';
      label.className = 'font-bold text-yellow-400';
    } else {
      label.textContent = '브릿지 대기';
      label.className = 'font-bold text-orange-400';
    }
  };
  update();
  window._bridgeStatusTimer = setInterval(update, 1500);
}

function fileIcon(f){
  if (f.endsWith('.js')) return 'fa-file-code text-yellow-400';
  if (f.endsWith('.ts')) return 'fa-file-code text-blue-400';
  if (f.endsWith('.json')) return 'fa-file-code text-orange-400';
  if (f.endsWith('.png')||f.endsWith('.jpg')) return 'fa-file-image text-purple-400';
  if (f.endsWith('.md')) return 'fa-file-alt text-gray-400';
  return 'fa-file';
}
function fileLang(f){
  if (f.endsWith('.js')) return 'javascript';
  if (f.endsWith('.ts')) return 'typescript';
  if (f.endsWith('.json')) return 'json';
  if (f.endsWith('.md')) return 'markdown';
  return 'plaintext';
}

function renderFileTree(){
  const tree = {};
  Object.keys(state.files).sort().forEach(path=>{
    const parts = path.split('/');
    let cur = tree;
    parts.forEach((p,i)=>{
      if (i===parts.length-1) cur[p] = path;
      else { cur[p] = cur[p]||{}; cur = cur[p]; }
    });
  });

  const filter = state.fileSearch?.trim().toLowerCase();
  function filterTree(node){
    const result = {};
    Object.entries(node).forEach(([k,v])=>{
      if (typeof v === 'string') {
        if (!filter || k.toLowerCase().includes(filter) || v.toLowerCase().includes(filter)) {
          result[k] = v;
        }
      } else {
        const child = filterTree(v);
        if (Object.keys(child).length > 0 || (!filter && k.toLowerCase().includes(filter))) {
          result[k] = child;
        }
      }
    });
    return result;
  }

  const treeRoot = filter ? filterTree(tree) : tree;
  function r(node, depth=0){
    return Object.entries(node).map(([k,v])=>{
      if (typeof v==='string') {
        const editing = state.editingStatus[v] || {};
        const others = Object.keys(editing).filter(u=>u!==state.user?.username);
        const badges = others.map(u=>{
          const color = getCollabColor(u);
          return `<span class="editing-dot" style="background:${color};box-shadow:0 0 4px ${color}" title="${u} 수정 중"></span>`;
        }).join('');
        const hasEditing = others.length > 0;
        return `<div class="file-tree-item ${v===state.currentFile?'active':''} ${hasEditing?'font-semibold':''}" style="padding-left:${8 + depth*12}px"
                     onclick="openFile('${v}')"
                     oncontextmenu="event.preventDefault();showFileMenu(event,'${v}')">
          <i class="fas ${fileIcon(v).split(' ')[0]} text-xs ${fileIcon(v).split(' ')[1]||''}"></i>
          <span class="flex-1 truncate">${k}</span>
          ${badges?`<span class="editing-badge" style="display:flex;gap:2px">${badges}</span>`:''}
          ${canDo('manage_files')?`<span class="file-actions"><i class="fas fa-pen" title="이름 변경" onclick="event.stopPropagation();renameFile('${v}')"></i><i class="fas fa-trash" title="삭제" onclick="event.stopPropagation();deleteFile('${v}')"></i></span>`:''}
        </div>`;
      } else {
        return `<div class="file-tree-item folder" style="padding-left:${8 + depth*12}px">
            <i class="fas fa-folder text-cyan-400 text-xs"></i>
            <span class="flex-1 truncate">${k}</span>
          </div>
          <div>${r(v, depth+1)}</div>`;
      }
    }).join('');
  }
  return r(treeRoot);
}

function renderChat(){
  return state.chat.map(m=>`
    <div class="chat-msg">
      <div class="flex items-baseline gap-2 mb-1">
        <span class="font-semibold text-sm" style="color:${getCollabColor(m.user)}">${m.user}</span>
        <span class="text-xs text-gray-500">${timeAgo(m.time)}</span>
      </div>
      <div class="text-sm">${escapeHtml(m.msg)}</div>
    </div>
  `).join('') || '<div class="text-center text-gray-500 text-sm py-8">아직 메시지가 없습니다</div>';
}

function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c])}

// ==================== Monaco 에디터 ====================
function initEditor(){
  if (!window.monaco){
    require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' }});
    require(['vs/editor/editor.main'], ()=>{
      setupMinecraftCompletion();
      defineThemes();
      createEditor();
    });
  } else createEditor();
}

function defineThemes(){
  monaco.editor.defineTheme('minescript', {
    base:'vs-dark', inherit:true,
    rules:[
      {token:'comment',foreground:'5c6370',fontStyle:'italic'},
      {token:'keyword',foreground:'a855f7'},
      {token:'string',foreground:'00ff88'},
      {token:'number',foreground:'ff9800'},
      {token:'identifier',foreground:'e4e9f5'},
    ],
    colors:{
      'editor.background':'#0a0e1a',
      'editor.lineHighlightBackground':'#0f1422',
      'editorLineNumber.foreground':'#3a4159',
      'editor.selectionBackground':'#00d4ff33',
      'editorGutter.background':'#0a0e1a',
    }
  });
  monaco.editor.defineTheme('minescript-light', {
    base:'vs', inherit:true, rules:[], colors:{}
  });
  monaco.editor.defineTheme('minescript-hc', {
    base:'hc-black', inherit:true, rules:[], colors:{}
  });
}

// Bedrock Script API 자동완성
function setupMinecraftCompletion(){
  const snippets = [
    // 모듈 import
    {label:'import @minecraft/server', insert:'import { world, system } from "@minecraft/server";', doc:'기본 서버 모듈 import'},
    {label:'import @minecraft/server-ui', insert:'import { ActionFormData, ModalFormData, MessageFormData } from "@minecraft/server-ui";', doc:'UI 모듈 import'},
    
    // 이벤트
    {label:'world.afterEvents.itemUse', insert:'world.afterEvents.itemUse.subscribe((event) => {\n\tconst player = event.source;\n\tconst item = event.itemStack;\n\t${0}\n});', doc:'아이템 사용 이벤트'},
    {label:'world.afterEvents.entityHurt', insert:'world.afterEvents.entityHurt.subscribe((event) => {\n\tconst entity = event.hurtEntity;\n\tconst damage = event.damage;\n\t${0}\n});', doc:'엔티티 피격 이벤트'},
    {label:'world.afterEvents.entityDie', insert:'world.afterEvents.entityDie.subscribe((event) => {\n\tconst entity = event.deadEntity;\n\t${0}\n});', doc:'엔티티 사망 이벤트'},
    {label:'world.afterEvents.playerSpawn', insert:'world.afterEvents.playerSpawn.subscribe((event) => {\n\tconst player = event.player;\n\tif (event.initialSpawn) {\n\t\t${0}\n\t}\n});', doc:'플레이어 첫 접속 이벤트'},
    {label:'world.afterEvents.playerLeave', insert:'world.afterEvents.playerLeave.subscribe((event) => {\n\tconst playerName = event.playerName;\n\t${0}\n});', doc:'플레이어 나감 이벤트'},
    {label:'world.afterEvents.chatSend', insert:'world.beforeEvents.chatSend.subscribe((event) => {\n\tconst sender = event.sender;\n\tconst message = event.message;\n\t${0}\n});', doc:'채팅 이벤트'},
    {label:'world.afterEvents.blockBreak', insert:'world.afterEvents.playerBreakBlock.subscribe((event) => {\n\tconst player = event.player;\n\tconst block = event.block;\n\t${0}\n});', doc:'블록 파괴 이벤트'},
    {label:'world.afterEvents.blockPlace', insert:'world.afterEvents.playerPlaceBlock.subscribe((event) => {\n\tconst player = event.player;\n\tconst block = event.block;\n\t${0}\n});', doc:'블록 설치 이벤트'},
    {label:'world.afterEvents.projectileHit', insert:'world.afterEvents.projectileHitEntity.subscribe((event) => {\n\tconst projectile = event.projectile;\n\tconst hit = event.getEntityHit();\n\t${0}\n});', doc:'발사체 충돌'},
    {label:'world.afterEvents.entitySpawn', insert:'world.afterEvents.entitySpawn.subscribe((event) => {\n\tconst entity = event.entity;\n\t${0}\n});', doc:'엔티티 생성 이벤트'},

    // system
    {label:'system.runInterval', insert:'system.runInterval(() => {\n\t${0}\n}, ${1:20});', doc:'반복 실행 (tick)'},
    {label:'system.runTimeout', insert:'system.runTimeout(() => {\n\t${0}\n}, ${1:20});', doc:'지연 실행 (tick)'},
    {label:'system.run', insert:'system.run(() => {\n\t${0}\n});', doc:'다음 tick에 실행'},
    
    // world 메서드
    {label:'world.sendMessage', insert:'world.sendMessage("${1:message}");', doc:'모든 플레이어에게 메시지'},
    {label:'world.getAllPlayers', insert:'world.getAllPlayers()', doc:'모든 플레이어 배열'},
    {label:'world.getDimension', insert:'world.getDimension("${1:overworld}")', doc:'차원 가져오기'},
    {label:'world.getPlayers', insert:'world.getPlayers({ ${1:tags: ["admin"]} })', doc:'필터로 플레이어 검색'},
    {label:'world.setDynamicProperty', insert:'world.setDynamicProperty("${1:key}", ${2:value});', doc:'동적 속성 저장'},
    {label:'world.getDynamicProperty', insert:'world.getDynamicProperty("${1:key}")', doc:'동적 속성 읽기'},

    // player 메서드
    {label:'player.sendMessage', insert:'player.sendMessage("${1:message}");', doc:'플레이어에게 메시지'},
    {label:'player.runCommand', insert:'player.runCommand("${1:command}");', doc:'커맨드 실행'},
    {label:'player.runCommandAsync', insert:'await player.runCommandAsync("${1:command}");', doc:'비동기 커맨드'},
    {label:'player.addTag', insert:'player.addTag("${1:tag}");', doc:'태그 추가'},
    {label:'player.hasTag', insert:'player.hasTag("${1:tag}")', doc:'태그 확인'},
    {label:'player.removeTag', insert:'player.removeTag("${1:tag}");', doc:'태그 제거'},
    {label:'player.applyDamage', insert:'player.applyDamage(${1:5});', doc:'데미지 입히기'},
    {label:'player.applyKnockback', insert:'player.applyKnockback(${1:0}, ${2:0}, ${3:5}, ${4:1});', doc:'넉백'},
    {label:'player.kill', insert:'player.kill();', doc:'즉시 사망'},
    {label:'player.teleport', insert:'player.teleport({ x: ${1:0}, y: ${2:64}, z: ${3:0} });', doc:'순간이동'},
    {label:'player.addEffect', insert:'player.addEffect("${1:speed}", ${2:200}, { amplifier: ${3:1} });', doc:'효과 추가'},
    {label:'player.getComponent', insert:'player.getComponent("${1:health}")', doc:'컴포넌트 가져오기'},

    // dimension
    {label:'dimension.createExplosion', insert:'dimension.createExplosion(${1:location}, ${2:3}, {\n\tbreaksBlocks: true,\n\tcausesFire: ${3:false}\n});', doc:'폭발 생성'},
    {label:'dimension.spawnEntity', insert:'dimension.spawnEntity("${1:minecraft:zombie}", ${2:location});', doc:'엔티티 스폰'},
    {label:'dimension.spawnItem', insert:'dimension.spawnItem(${1:itemStack}, ${2:location});', doc:'아이템 드롭'},
    {label:'dimension.getBlock', insert:'dimension.getBlock(${1:location})', doc:'블록 가져오기'},
    {label:'dimension.setBlockType', insert:'dimension.setBlockType(${1:location}, "${2:minecraft:stone}");', doc:'블록 설치'},
    {label:'dimension.spawnParticle', insert:'dimension.spawnParticle("${1:minecraft:basic_flame_particle}", ${2:location});', doc:'파티클'},
    {label:'dimension.playSound', insert:'dimension.playSound("${1:random.explode}", ${2:location});', doc:'사운드 재생'},
    
    // ItemStack
    {label:'new ItemStack', insert:'new ItemStack("${1:minecraft:diamond_sword}", ${2:1})', doc:'아이템 스택 생성'},
    
    // UI
    {label:'ActionFormData', insert:'const form = new ActionFormData()\n\t.title("${1:Title}")\n\t.body("${2:Body}")\n\t.button("${3:Option 1}");\n\nform.show(${4:player}).then(response => {\n\tif (response.canceled) return;\n\t${0}\n});', doc:'액션 폼 UI'},
    {label:'ModalFormData', insert:'const form = new ModalFormData()\n\t.title("${1:Title}")\n\t.textField("${2:Label}", "${3:Placeholder}")\n\t.toggle("${4:Toggle}", false);\n\nform.show(${5:player}).then(response => {\n\tif (response.canceled) return;\n\tconst [text, toggle] = response.formValues;\n\t${0}\n});', doc:'모달 폼'},
    {label:'MessageFormData', insert:'const form = new MessageFormData()\n\t.title("${1:Title}")\n\t.body("${2:Body}")\n\t.button1("${3:Yes}")\n\t.button2("${4:No}");\n\nform.show(${5:player}).then(response => {\n\t${0}\n});', doc:'메시지 폼'},
    
    // 더 많은 이벤트
    {label:'world.beforeEvents.chatSend', insert:'world.beforeEvents.chatSend.subscribe((event) => {\n\tconst player = event.sender;\n\tconst message = event.message;\n\t${0}\n});', doc:'채팅 전송 전 이벤트'},
    {label:'world.beforeEvents.itemUse', insert:'world.beforeEvents.itemUse.subscribe((event) => {\n\tconst player = event.source;\n\tconst item = event.itemStack;\n\t${0}\n});', doc:'아이템 사용 전 이벤트'},
    {label:'world.afterEvents.playerJoin', insert:'world.afterEvents.playerJoin.subscribe((event) => {\n\tconst playerName = event.playerName;\n\t${0}\n});', doc:'플레이어 접속 이벤트'},
    {label:'world.afterEvents.entityHitEntity', insert:'world.afterEvents.entityHitEntity.subscribe((event) => {\n\tconst attacker = event.damagingEntity;\n\tconst target = event.hitEntity;\n\t${0}\n});', doc:'엔티티가 엔티티를 공격'},
    {label:'world.afterEvents.entityHitBlock', insert:'world.afterEvents.entityHitBlock.subscribe((event) => {\n\tconst entity = event.damagingEntity;\n\tconst block = event.hitBlock;\n\t${0}\n});', doc:'엔티티가 블록을 타격'},
    {label:'world.afterEvents.itemStartUse', insert:'world.afterEvents.itemStartUse.subscribe((event) => {\n\tconst player = event.source;\n\tconst item = event.itemStack;\n\t${0}\n});', doc:'아이템 사용 시작'},
    {label:'world.afterEvents.itemStopUse', insert:'world.afterEvents.itemStopUse.subscribe((event) => {\n\tconst player = event.source;\n\tconst item = event.itemStack;\n\t${0}\n});', doc:'아이템 사용 중지'},
    {label:'world.afterEvents.itemCompleteUse', insert:'world.afterEvents.itemCompleteUse.subscribe((event) => {\n\tconst player = event.source;\n\tconst item = event.itemStack;\n\t${0}\n});', doc:'아이템 사용 완료'},
    {label:'world.afterEvents.playerDimensionChange', insert:'world.afterEvents.playerDimensionChange.subscribe((event) => {\n\tconst player = event.player;\n\tconst from = event.fromDimension;\n\tconst to = event.toDimension;\n\t${0}\n});', doc:'차원 이동 이벤트'},
    {label:'world.afterEvents.weatherChange', insert:'world.afterEvents.weatherChange.subscribe((event) => {\n\tconst dimension = event.dimension;\n\t${0}\n});', doc:'날씨 변경 이벤트'},
    
    // 자주 쓰는 API 패턴
    {label:'getPlayers by tag', insert:'const players = world.getPlayers({ tags: ["${1:tag}"] });\nfor (const player of players) {\n\t${0}\n}', doc:'태그로 플레이어 찾기'},
    {label:'scoreboard objective', insert:'let objective = world.scoreboard.getObjective("${1:points}");\nif (!objective) objective = world.scoreboard.addObjective("${1:points}", "${2:Points}");\n${0}', doc:'스코어보드 objective 준비'},
    {label:'scoreboard add score', insert:'const objective = world.scoreboard.getObjective("${1:points}");\nconst score = objective.getScore(${2:player}) ?? 0;\nobjective.setScore(${2:player}, score + ${3:1});', doc:'스코어보드 점수 증가'},
    {label:'player inventory', insert:'const inventory = ${1:player}.getComponent("minecraft:inventory").container;\nfor (let i = 0; i < inventory.size; i++) {\n\tconst item = inventory.getItem(i);\n\t${0}\n}', doc:'플레이어 인벤토리 순회'},
    {label:'player equipment', insert:'const equippable = ${1:player}.getComponent("minecraft:equippable");\nconst mainhand = equippable.getEquipment("Mainhand");\n${0}', doc:'장비 컴포넌트'},
    {label:'spawn item', insert:'const item = new ItemStack("${1:minecraft:diamond}", ${2:1});\n${3:player}.dimension.spawnItem(item, ${3:player}.location);', doc:'아이템 드롭'},
    {label:'run command safe', insert:'try {\n\tawait ${1:player}.runCommandAsync("${2:say hello}");\n} catch (e) {\n\tconsole.warn(e);\n}', doc:'명령어 안전 실행'},
    {label:'random chance', insert:'if (Math.random() < ${1:0.25}) {\n\t${0}\n}', doc:'확률 조건'},
    {label:'nearby entities', insert:'const entities = ${1:player}.dimension.getEntities({\n\tlocation: ${1:player}.location,\n\tmaxDistance: ${2:8},\n\texcludeTypes: ["minecraft:player"]\n});\nfor (const entity of entities) {\n\t${0}\n}', doc:'주변 엔티티 검색'},
    {label:'dynamic property setup', insert:'world.afterEvents.worldInitialize.subscribe((event) => {\n\tconst def = new DynamicPropertiesDefinition();\n\tdef.defineString("${1:key}", ${2:64});\n\tevent.propertyRegistry.registerWorldDynamicProperties(def);\n});', doc:'동적 속성 등록'},
    {label:'actionbar', insert:'${1:player}.onScreenDisplay.setActionBar("${2:message}");', doc:'액션바 메시지'},
    {label:'title', insert:'${1:player}.onScreenDisplay.setTitle("${2:Title}", {\n\tsubtitle: "${3:Subtitle}",\n\tfadeInDuration: 10,\n\tstayDuration: 40,\n\tfadeOutDuration: 10\n});', doc:'타이틀 표시'},
    {label:'play sound', insert:'${1:player}.playSound("${2:random.orb}", { pitch: 1, volume: 1 });', doc:'플레이어에게 사운드 재생'},
    {label:'add cooldown tag', insert:'if (${1:player}.hasTag("${2:cooldown}")) return;\n${1:player}.addTag("${2:cooldown}");\nsystem.runTimeout(() => ${1:player}.removeTag("${2:cooldown}"), ${3:20});', doc:'태그 기반 쿨타임'},
    
    // 패턴
    {label:'try-catch', insert:'try {\n\t${1}\n} catch (e) {\n\tconsole.error(e);\n}', doc:'try-catch'},
    {label:'for player loop', insert:'for (const player of world.getAllPlayers()) {\n\t${0}\n}', doc:'모든 플레이어 순회'},
    {label:'Vector3', insert:'{ x: ${1:0}, y: ${2:0}, z: ${3:0} }', doc:'Vector3 좌표'},
  ];

  monaco.languages.registerCompletionItemProvider('javascript', {
    triggerCharacters: ['.', '"', "'"],
    provideCompletionItems: (model, position)=>{
      return {
        suggestions: snippets.map(s=>({
          label: s.label,
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: s.insert,
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          documentation: { value: '**'+s.label+'**\n\n'+s.doc },
          detail: 'Bedrock Script API',
        }))
      };
    }
  });

  // JSON 자동완성
  monaco.languages.registerCompletionItemProvider('json', {
    provideCompletionItems: ()=>({
      suggestions: [
        {label:'minecraft:item', kind:monaco.languages.CompletionItemKind.Snippet,
         insertText:'"minecraft:item": {\n\t"description": {\n\t\t"identifier": "${1:custom:my_item}"\n\t},\n\t"components": {\n\t\t${0}\n\t}\n}',
         insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, detail:'Item 정의'},
        {label:'minecraft:entity', kind:monaco.languages.CompletionItemKind.Snippet,
         insertText:'"minecraft:entity": {\n\t"description": {\n\t\t"identifier": "${1:custom:my_entity}",\n\t\t"is_spawnable": true,\n\t\t"is_summonable": true\n\t},\n\t"components": {\n\t\t${0}\n\t}\n}',
         insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, detail:'Entity 정의'},
        {label:'minecraft:health', insertText:'"minecraft:health": { "value": ${1:20} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:damage', insertText:'"minecraft:damage": ${1:5}', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:movement', insertText:'"minecraft:movement": { "value": ${1:0.25} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:max_stack_size', insertText:'"minecraft:max_stack_size": ${1:64}', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:durability', insertText:'"minecraft:durability": { "max_durability": ${1:500} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'manifest behavior pack', kind:monaco.languages.CompletionItemKind.Snippet,
         insertText:'{\n\t"format_version": 2,\n\t"header": {\n\t\t"name": "${1:My Pack}",\n\t\t"description": "${2:Description}",\n\t\t"uuid": "${3:00000000-0000-0000-0000-000000000000}",\n\t\t"version": [1, 0, 0],\n\t\t"min_engine_version": [1, 20, 0]\n\t},\n\t"modules": [{\n\t\t"type": "script",\n\t\t"language": "javascript",\n\t\t"uuid": "${4:00000000-0000-0000-0000-000000000001}",\n\t\t"version": [1, 0, 0],\n\t\t"entry": "scripts/main.js"\n\t}],\n\t"dependencies": [{\n\t\t"module_name": "@minecraft/server",\n\t\t"version": "${5:1.10.0}"\n\t}]\n}',
         insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, detail:'Behavior Pack manifest'},
        {label:'minecraft:food', insertText:'"minecraft:food": { "nutrition": ${1:4}, "saturation_modifier": ${2:0.6} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:display_name', insertText:'"minecraft:display_name": { "value": "${1:Custom Name}" }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:icon', insertText:'"minecraft:icon": { "texture": "${1:item_texture}" }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:hand_equipped', insertText:'"minecraft:hand_equipped": ${1:true}', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:cooldown', insertText:'"minecraft:cooldown": { "category": "${1:item}", "duration": ${2:1.0} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:wearable', insertText:'"minecraft:wearable": { "slot": "slot.armor.${1:head}", "protection": ${2:2} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:projectile', insertText:'"minecraft:projectile": { "projectile_entity": "${1:minecraft:snowball}", "minimum_critical_power": ${2:1.0} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:interact', insertText:'"minecraft:interact": {\n\t"interactions": [{\n\t\t"on_interact": { "event": "${1:custom:event}" },\n\t\t"interact_text": "${2:Use}"\n\t}]\n}', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:loot', insertText:'"minecraft:loot": { "table": "loot_tables/${1:entities/example}.json" }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:behavior.melee_attack', insertText:'"minecraft:behavior.melee_attack": { "priority": ${1:3}, "speed_multiplier": ${2:1.2}, "track_target": true }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:behavior.random_stroll', insertText:'"minecraft:behavior.random_stroll": { "priority": ${1:6}, "speed_multiplier": ${2:1.0} }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
        {label:'minecraft:type_family', insertText:'"minecraft:type_family": { "family": ["${1:monster}", "${2:mob}"] }', insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, kind:monaco.languages.CompletionItemKind.Property},
      ]
    })
  });
}

function createEditor(){
  const el = document.getElementById('editor');
  if (!el || !state.currentFile) return;
  
  const s = state.settings || {};
  if (state.editor) state.editor.dispose();
  state.collabWidgets = {};
  
  state.editor = monaco.editor.create(el, {
    value: state.files[state.currentFile] || '',
    language: fileLang(state.currentFile),
    theme: s.theme || 'minescript',
    fontFamily: s.fontFamily || 'JetBrains Mono, monospace',
    fontSize: s.fontSize || 14,
    minimap: { enabled: s.minimap !== false },
    automaticLayout: true,
    folding: true,
    lineNumbers: s.lineNumbers === false ? 'off' : 'on',
    scrollBeyondLastLine: false,
    wordWrap: s.wordWrap ? 'on' : 'off',
    formatOnPaste: s.formatOnPaste !== false,
    tabSize: s.tabSize || 2,
    renderWhitespace: s.renderWhitespace || 'selection',
    cursorBlinking: s.cursorBlinking || 'smooth',
    cursorStyle: s.cursorStyle || 'line',
    quickSuggestions: s.autoComplete !== false,
    glyphMargin: true,
    bracketPairColorization: { enabled: true },
    smoothScrolling: true,
  });
  
  state.savedLines = new Set();
  state.modifiedLines = new Set();
  
  // 편집 중 표시 전송
  if (state.socket) {
    state.socket.emit('editing_file', {pid: state.project.id, user: state.user?.username, file: state.currentFile});
  }
  
  let saveTimer;
  state.editor.onDidChangeModelContent((ev)=>{
    if (state.applyingRemoteChange) return;
    const pos = state.editor.getPosition();
    state.dirtyTabs.add(state.currentFile);
    document.getElementById('save-status').textContent = '● 저장 중...';
    document.getElementById('save-status').className = 'text-orange-400';
    
    // 변경된 줄 표시
    ev.changes.forEach(c=>{
      const startLine = c.range.startLineNumber;
      const endLine = c.range.endLineNumber + (c.text.split('\n').length - 1);
      for (let l=startLine; l<=endLine; l++) {
        state.modifiedLines.add(l);
        state.savedLines.delete(l);
      }
    });
    updateLineDecorations();
    
    if (state.settings?.autoSave !== false) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(saveCurrentFile, state.settings?.autoSaveDelay || 1000);
    }
    if (state.socket) {
      state.socket.emit('code_change', {
        pid: state.project.id,
        user: state.user?.username,
        file: state.currentFile,
        content: state.editor.getValue(),
        editingLine: pos?.lineNumber || null,
        changes: ev.changes.map(c=>({
          startLine: c.range.startLineNumber,
          endLine: c.range.endLineNumber + (c.text.split('\n').length - 1),
          textLength: c.text.length,
        })),
      });
      state.socket.emit('cursor_move', {
        pid: state.project.id,
        user: state.user?.username,
        file: state.currentFile,
        line: pos?.lineNumber || 1,
        col: pos?.column || 1,
        editingLine: pos?.lineNumber || null,
        selection: null,
      });
    }
  });
  
  // 커서 위치 전송 & 표시
  state.editor.onDidChangeCursorPosition(e=>{
    document.getElementById('cursor-pos').textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
    if (state.socket) {
      const sel = state.editor.getSelection();
      state.socket.emit('cursor_move', {
        pid: state.project.id,
        user: state.user?.username,
        file: state.currentFile,
        line: e.position.lineNumber,
        col: e.position.column,
        editingLine: e.position.lineNumber,
        selection: sel && !sel.isEmpty() ? {
          startLine: sel.startLineNumber, startCol: sel.startColumn,
          endLine: sel.endLineNumber, endCol: sel.endColumn,
        } : null,
      });
    }
  });
  
  // 단축키: Ctrl+S 저장
  state.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, ()=>{
    saveCurrentFile(true);
  });
  
  // 다른 협업자 커서 다시 그리기
  redrawCollabCursors();
}

function updateLineDecorations(){
  if (!state.editor) return;
  const decos = [];
  state.modifiedLines.forEach(l=>{
    decos.push({
      range: new monaco.Range(l,1,l,1),
      options: { isWholeLine: false, glyphMarginClassName: 'modified-line-glyph' }
    });
  });
  state.savedLines.forEach(l=>{
    if (!state.modifiedLines.has(l)) {
      decos.push({
        range: new monaco.Range(l,1,l,1),
        options: { isWholeLine: false, glyphMarginClassName: 'saved-line-glyph' }
      });
    }
  });
  state._lineDecorationIds = state.editor.deltaDecorations(state._lineDecorationIds||[], decos);
}

function applyRemoteFileContent(path, content){
  state.files[path] = content;
  if (path !== state.currentFile || !state.editor) return;
  if (state.editor.getValue() === content) return;
  const pos = state.editor.getPosition();
  const sel = state.editor.getSelection();
  state.applyingRemoteChange = true;
  try {
    state.editor.setValue(content);
  } finally {
    state.applyingRemoteChange = false;
  }
  if (pos) state.editor.setPosition(pos);
  if (sel) state.editor.setSelection(sel);
}

async function saveCurrentFile(manual){
  if (!state.currentFile || !state.editor) return;
  const content = state.editor.getValue();
  state.files[state.currentFile] = content;
  
  // 모든 modified 라인을 saved로 이동
  state.modifiedLines.forEach(l=>state.savedLines.add(l));
  state.modifiedLines = new Set();
  updateLineDecorations();
  
  await api.post(`/api/projects/${state.project.id}/files`, {
    path: state.currentFile, content,
  });
  
  state.dirtyTabs.delete(state.currentFile);
  document.getElementById('save-status').textContent = '● 저장됨';
  document.getElementById('save-status').className = 'text-green-400';
  
  if (manual) notify('저장됨','success');
  updateTabsUI();
}

function updateTabsUI(){
  const tabs = document.querySelectorAll('.tab');
  // 부분 업데이트 대신 단순 재렌더가 더 안전
}

function openFile(path){
  if (state.currentFile === path) return;
  if (!state.openTabs.includes(path)) state.openTabs.push(path);
  state.currentFile = path;
  render();
  setTimeout(initEditor, 50);
}
function switchTab(path){ openFile(path); }
function closeTab(path){
  if (state.dirtyTabs.has(path)) {
    if (!confirm('저장하지 않은 변경사항이 있습니다. 닫을까요?')) return;
  }
  state.openTabs = state.openTabs.filter(t=>t!==path);
  if (state.currentFile===path) state.currentFile = state.openTabs[0] || null;
  render();
  if (state.currentFile) setTimeout(initEditor,50);
}

// 파일 컨텍스트 메뉴
function showFileMenu(e, path){
  closeContextMenu();
  const canEdit = canDo('manage_files');
  
  const items = [
    {icon:'fa-file', label:'열기', action:()=>openFile(path)},
    ...(canEdit?[
      {icon:'fa-pen', label:'이름 변경', action:()=>renameFile(path)},
      {icon:'fa-copy', label:'복제', action:()=>duplicateFile(path)},
      {sep:true},
      {icon:'fa-trash', label:'삭제', danger:true, action:()=>deleteFile(path)},
    ]:[]),
    {sep:true},
    {icon:'fa-download', label:'다운로드', action:()=>downloadSingle(path)},
  ];
  
  const menu = document.createElement('div');
  menu.className = 'context-menu';
  menu.style.left = e.pageX+'px';
  menu.style.top = e.pageY+'px';
  menu.innerHTML = items.map(i=>i.sep?'<div class="context-divider"></div>':
    `<div class="context-menu-item ${i.danger?'danger':''}" data-action="${items.indexOf(i)}">
      <i class="fas ${i.icon}"></i> ${i.label}
    </div>`).join('');
  
  document.getElementById('context-menu-host').appendChild(menu);
  menu.querySelectorAll('.context-menu-item').forEach(el=>{
    el.onclick = ()=>{
      const idx = parseInt(el.dataset.action);
      items[idx].action();
      closeContextMenu();
    };
  });
  setTimeout(()=>document.addEventListener('click', closeContextMenu, {once:true}), 50);
}

function closeContextMenu(){
  document.getElementById('context-menu-host').innerHTML = '';
}

async function renameFile(path){
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-md w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold gradient-text"><i class="fas fa-edit"></i> 파일 이름 변경</h3>
          <button onclick="closeModal()" class="text-gray-400"><i class="fas fa-times"></i></button>
        </div>
        <p class="text-sm text-gray-400 mb-2">기존 경로:</p>
        <div class="card p-2 bg-gray-900/50 mb-4">
          <div class="font-mono text-xs text-gray-400 break-all">${escapeHtml(path)}</div>
        </div>
        <p class="text-sm text-gray-400 mb-2">새 경로:</p>
        <input id="rename-new-path" class="input mb-4" value="${escapeHtml(path)}" autofocus>
        <div class="flex gap-2">
          <button class="mc-btn flex-1" onclick="confirmRenameFile('${path}')">이름 변경</button>
          <button class="neon-btn" onclick="closeModal()">취소</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
  const inp = document.getElementById('rename-new-path');
  inp.select();
  inp.onkeydown = (e)=>{if(e.key==='Enter')confirmRenameFile(path)};
}

async function confirmRenameFile(path){
  const newName = document.getElementById('rename-new-path')?.value?.trim();
  if (!newName || newName===path) return closeModal();
  if (newName in state.files && newName !== path) return notify('이미 존재하는 파일명입니다','error');
  
  const r = await api.post(`/api/projects/${state.project.id}/files/rename`, {old:path, new:newName});
  if (r.ok){
    state.files[newName] = state.files[path];
    delete state.files[path];
    state.openTabs = state.openTabs.map(t=>t===path?newName:t);
    if (state.currentFile===path) state.currentFile = newName;
    closeModal();
    notify('파일 이름 변경됨','success');
    render(); setTimeout(initEditor,50);
  } else notify(r.error || '변경 실패','error');
}

async function duplicateFile(path){
  const newName = path.replace(/(\.[^./]+)?$/, '_copy$1');
  state.files[newName] = state.files[path];
  await api.post(`/api/projects/${state.project.id}/files`, {path:newName, content:state.files[path]});
  notify('복제됨','success');
  render(); setTimeout(initEditor,50);
}

async function deleteFile(path){
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-md w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold text-pink-400"><i class="fas fa-trash"></i> 파일 삭제</h3>
          <button onclick="closeModal()" class="text-gray-400"><i class="fas fa-times"></i></button>
        </div>
        <p class="text-sm text-gray-400 mb-6">정말로 이 파일을 삭제하시겠습니까?</p>
        <div class="card p-3 bg-pink-500/10 border-pink-500/30 mb-4">
          <div class="font-mono text-sm text-pink-300 break-all">${escapeHtml(path)}</div>
        </div>
        <p class="text-xs text-gray-500 mb-4">⚠️ 이 작업은 취소할 수 없습니다.</p>
        <div class="flex gap-2">
          <button class="neon-btn pink flex-1" onclick="confirmDeleteFile('${path}')"><i class="fas fa-check"></i> 삭제</button>
          <button class="neon-btn" onclick="closeModal()">취소</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
}

async function confirmDeleteFile(path){
  const r = await api.del(`/api/projects/${state.project.id}/files`, {path});
  if (r.ok){
    delete state.files[path];
    state.openTabs = state.openTabs.filter(t=>t!==path);
    if (state.currentFile===path) state.currentFile = state.openTabs[0]||null;
    closeModal();
    notify('파일 삭제됨','success');
    render(); if (state.currentFile) setTimeout(initEditor,50);
  } else notify(r.error || '삭제 실패','error');
}

function downloadSingle(path){
  const content = state.files[path];
  const blob = new Blob([content], {type:'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = path.split('/').pop();
  a.click();
}

async function newFile(){
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-md w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold gradient-text"><i class="fas fa-file-plus"></i> 새 파일</h3>
          <button onclick="closeModal()" class="text-gray-400"><i class="fas fa-times"></i></button>
        </div>
        <p class="text-sm text-gray-400 mb-4">새 파일의 경로를 입력하세요</p>
        <input id="new-file-path" class="input mb-4" placeholder="예: scripts/util.js" autofocus>
        <div class="text-xs text-gray-500 mb-4">
          💡 팁: 폴더는 자동으로 생성됩니다 (예: <code style="color:#00ff88">items/custom.json</code>)
        </div>
        <div class="flex gap-2">
          <button class="mc-btn flex-1" onclick="doCreateFile()">생성</button>
          <button class="neon-btn" onclick="closeModal()">취소</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
  document.getElementById('new-file-path').onkeydown = (e)=>{if(e.key==='Enter')doCreateFile()};
}

async function doCreateFile(){
  const path = document.getElementById('new-file-path')?.value?.trim();
  if (!path) return notify('파일 경로를 입력해주세요','warn');
  if (path in state.files) return notify('이미 존재하는 파일입니다','error');
  if (!path.match(/^[a-zA-Z0-9._\-/]+$/)) return notify('유효하지 않은 파일 경로입니다','error');
  
  state.files[path] = '';
  const r = await api.post(`/api/projects/${state.project.id}/files`, {path, content:''});
  if (r.ok) {
    closeModal();
    notify('파일 생성됨','success');
    openFile(path);
  } else notify(r.error || '생성 실패','error');
}

async function newFolder(){
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-md w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold gradient-text"><i class="fas fa-folder-plus"></i> 새 폴더</h3>
          <button onclick="closeModal()" class="text-gray-400"><i class="fas fa-times"></i></button>
        </div>
        <p class="text-sm text-gray-400 mb-4">폴더의 첫 파일 경로를 입력하세요</p>
        <input id="new-folder-path" class="input mb-4" placeholder="예: textures/items/icon.json" autofocus>
        <div class="text-xs text-gray-500 mb-4">
          💡 팁: 폴더 안에 첫 파일을 생성합니다. 추가 파일은 나중에 추가할 수 있습니다.
        </div>
        <div class="flex gap-2">
          <button class="mc-btn flex-1" onclick="doCreateFolder()">생성</button>
          <button class="neon-btn" onclick="closeModal()">취소</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
  document.getElementById('new-folder-path').onkeydown = (e)=>{if(e.key==='Enter')doCreateFolder()};
}

async function doCreateFolder(){
  const path = document.getElementById('new-folder-path')?.value?.trim();
  if (!path) return notify('파일 경로를 입력해주세요','warn');
  if (path in state.files) return notify('이미 존재하는 파일입니다','error');
  
  state.files[path] = '';
  const r = await api.post(`/api/projects/${state.project.id}/files`, {path, content:''});
  if (r.ok) {
    closeModal();
    notify('폴더 및 파일 생성됨','success');
    openFile(path);
  } else notify(r.error || '생성 실패','error');
}

async function likeProject(){
  if (!state.user){ location.hash='login'; return; }
  const r = await api.post(`/api/projects/${state.project.id}/like`);
  if (r.liked) {
    if (!state.project.likes.includes(state.user.username)) state.project.likes.push(state.user.username);
  } else {
    state.project.likes = state.project.likes.filter(u=>u!==state.user.username);
  }
  notify(r.liked?'❤':'좋아요 취소','success');
  render(); setTimeout(initEditor,50);
}

function exportProject(){
  window.location.href = `/api/projects/${state.project.id}/export`;
  notify('다운로드 시작!','success');
}

async function runBuild(){
  notify('빌드 중...','info');
  const r = await api.get(`/api/projects/${state.project.id}/build`);
  showBuildModal(r);
}

function showBuildModal(r){
  const ok = r.success;
  const html = `
    <div class="modal-bg" onclick="if(event.target===this)closeModal()">
      <div class="glass-strong rounded-2xl p-6 max-w-2xl w-full mx-4" onclick="event.stopPropagation()">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-xl font-bold ${ok?'text-green-400':'text-pink-400'}">
            <i class="fas ${ok?'fa-check-circle':'fa-times-circle'}"></i> 빌드 ${ok?'성공':'실패'}
          </h3>
          <button onclick="closeModal()" class="text-gray-400"><i class="fas fa-times"></i></button>
        </div>
        <div class="text-sm text-gray-400 mb-4">파일 ${r.files}개 | 오류 ${r.errors.length}개 | 경고 ${r.warnings.length}개</div>
        ${r.errors.map(e=>`<div class="card p-3 mb-2" style="border-color:#ff3b8b">
          <div class="text-pink-400 font-mono text-sm">❌ ${e.file}</div>
          <div class="text-sm">${e.msg}</div></div>`).join('')}
        ${r.warnings.map(w=>`<div class="card p-3 mb-2" style="border-color:#ff9800">
          <div class="text-orange-400 font-mono text-sm">⚠ ${w.file}</div>
          <div class="text-sm">${w.msg}</div></div>`).join('')}
        ${ok?'<div class="text-center py-4 text-3xl">🎉</div>':''}
        <div class="text-right">
          ${ok?'<button class="mc-btn" onclick="closeModal();exportProject()">📦 Export</button>':''}
          <button class="neon-btn ml-2" onclick="closeModal()">닫기</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', `<div id="modal">${html}</div>`);
}
function closeModal(){ document.getElementById('modal')?.remove(); }

window._aiAgentHistory = window._aiAgentHistory || [];

function ensureAIPanel(){
  if (document.getElementById('ai-panel')) return;
  const hasCode = (state.editor?.getModel?.()?.getValue?.() || '').trim().length > 0;
  const isCodeFile = state.currentFile && /\.(js|ts|json)$/i.test(state.currentFile);
  const extraTabs = (hasCode && isCodeFile) ? `
    <button type="button" class="ai-tab" onclick="switchAITab('analyze')">analyze</button>
    <button type="button" class="ai-tab" onclick="switchAITab('explain')">explain</button>
    <button type="button" class="ai-tab" onclick="switchAITab('fix')">fix</button>` : '';
  const html = `
    <div id="ai-overlay" class="ai-overlay" onclick="closeAIPanel()"></div>
    <aside id="ai-panel">
      <div class="ai-panel-header flex items-center justify-between">
        <div class="ai-panel-title"><i class="fas fa-terminal"></i> <span class="brand">CoBuddy</span> agent</div>
        <button type="button" class="text-gray-400 hover:text-white" onclick="closeAIPanel()"><i class="fas fa-times"></i></button>
      </div>
      <div class="px-3 py-2 flex flex-wrap gap-2 items-center" style="border-bottom:1px solid #21262d">
        <span class="ai-provider-badge"><i class="fas fa-bolt"></i> Qianfan</span>
        <select id="ai-provider" class="input" style="flex:1;min-width:100px;padding:6px 10px;font-size:11px;font-family:'JetBrains Mono',monospace" onchange="onAIProviderChange()">
          <option value="cobuddy" selected>CoBuddy</option>
          <option value="openai">OpenAI</option>
        </select>
        <select id="ai-model" class="input" style="flex:1;min-width:100px;padding:6px 10px;font-size:11px;font-family:'JetBrains Mono',monospace">
          <option value="cobuddy" selected>cobuddy:free</option>
        </select>
      </div>
      <div class="ai-tab-bar">
        <button type="button" class="ai-tab active" onclick="switchAITab('agent')">agent</button>
        <button type="button" class="ai-tab" onclick="switchAITab('generate')">generate</button>
        ${extraTabs}
      </div>
      <div class="ai-panel-body flex flex-col" style="min-height:0">
        <div id="ai-tab-agent" class="ai-tab-content flex flex-col" style="min-height:0">
          <div id="ai-agent-chat" class="ai-agent-chat mb-3">
            <div class="ai-msg assistant"><div class="role">// CoBuddy</div>요청한 파일만 수정합니다. 예: manifest.json 작성해줘 · scripts/test.js 추가해줘</div>
          </div>
          <div class="flex flex-wrap gap-1 mb-2">
            <button type="button" class="neon-btn" style="font-size:10px;padding:4px 8px" onclick="setAgentPrompt('manifest.json 작성해줘')">manifest</button>
            <button type="button" class="neon-btn" style="font-size:10px;padding:4px 8px" onclick="setAgentPrompt('블럭 뽀면 킥하는 스크립트 작성해줘')">블럭킥</button>
            <button type="button" class="neon-btn" style="font-size:10px;padding:4px 8px" onclick="setAgentPrompt('scripts/util.js 파일 추가해줘')">파일추가</button>
            <button type="button" class="neon-btn" style="font-size:10px;padding:4px 8px" onclick="setAgentPrompt('오류 고쳐줘')">오류수정</button>
          </div>
          <div id="ai-terminal" class="ai-terminal hidden mb-3"></div>
          <textarea id="ai-agent-prompt" class="ai-code-input" rows="3" placeholder="예: 베드락 블럭 부수면 킥 / 오류 고쳐줘 (Enter)" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();runAIAgent();}"></textarea>
          <button type="button" id="ai-agent-btn" class="ai-send-btn" onclick="runAIAgent()"><i class="fas fa-play"></i> Run Agent</button>
        </div>
        <div id="ai-tab-generate" class="ai-tab-content hidden">
          <textarea id="ai-prompt" class="ai-code-input mb-2" rows="4" placeholder="코드 생성 요청"></textarea>
          <button type="button" class="ai-send-btn" onclick="askAI('generate')">Generate</button>
        </div>
        ${hasCode && isCodeFile ? `
        <div id="ai-tab-analyze" class="ai-tab-content hidden"><button type="button" class="ai-send-btn" onclick="askAI('analyze')">Analyze</button></div>
        <div id="ai-tab-explain" class="ai-tab-content hidden"><button type="button" class="ai-send-btn" onclick="askAI('explain')">Explain</button></div>
        <div id="ai-tab-fix" class="ai-tab-content hidden">
          <input id="ai-issue" class="ai-code-input mb-2" placeholder="issue (optional)">
          <button type="button" class="ai-send-btn" onclick="askAI('fix')">Fix</button>
        </div>` : ''}
        <div id="ai-loading" class="hidden mt-4 text-center py-4"><i class="fas fa-circle-notch fa-spin text-cyan-400"></i></div>
        <div id="ai-result" class="hidden mt-4">
          <div id="ai-metadata" class="text-xs text-gray-500 mb-2 font-mono hidden"></div>
          <div id="ai-explanation" class="text-xs text-gray-400 mb-2"></div>
          <div id="ai-issues" class="hidden mb-2 text-xs"></div>
          <div id="ai-optimizations" class="hidden mb-2 text-xs"></div>
          <div id="ai-suggestions" class="hidden mb-2 text-xs"></div>
          <div id="ai-analysis" class="hidden mb-2 text-xs"></div>
          <pre id="ai-code" class="ai-result-code" data-lang="js"></pre>
          <div class="flex gap-2 mt-2">
            <button type="button" class="neon-btn flex-1 text-xs" onclick="insertAICode()">Insert</button>
            <button type="button" class="neon-btn text-xs" onclick="copyAICode()">Copy</button>
          </div>
        </div>
      </div>
    </aside>`;
  document.body.insertAdjacentHTML('beforeend', html);
  onAIProviderChange();
}

function openAIPanel(){
  if (!state.project) return notify('프로젝트를 먼저 열어주세요','warn');
  ensureAIPanel();
  document.getElementById('ai-overlay')?.classList.add('open');
  document.getElementById('ai-panel')?.classList.add('open');
  switchAITab('agent');
  document.getElementById('ai-agent-prompt')?.focus();
}
function openAI(){ openAIPanel(); }
function closeAIPanel(){
  document.getElementById('ai-overlay')?.classList.remove('open');
  document.getElementById('ai-panel')?.classList.remove('open');
}
function onAIProviderChange(){
  const provider = document.getElementById('ai-provider')?.value || 'cobuddy';
  const modelSel = document.getElementById('ai-model');
  if (!modelSel) return;
  if (provider === 'cobuddy') {
    modelSel.innerHTML = '<option value="cobuddy" selected>cobuddy:free</option>';
  } else {
    modelSel.innerHTML = '<option value="gpt-5.5-pro">gpt-4o (pro)</option><option value="gpt-4o">gpt-4o</option>';
  }
}
function appendAgentMsg(role, text){
  const chat = document.getElementById('ai-agent-chat');
  if (!chat) return;
  const el = document.createElement('div');
  el.className = 'ai-msg ' + role;
  if (role === 'assistant') {
    el.innerHTML = '<div class="role">// CoBuddy</div>' + escapeHtml(text).replace(/\n/g,'<br>');
  } else {
    el.textContent = text;
  }
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
}
function escapeHtml(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function logAgentTerminal(lines){
  const term = document.getElementById('ai-terminal');
  if (!term) return;
  term.classList.remove('hidden');
  term.innerHTML = lines.map(l=>{
    const cls = l.ok ? 'ok' : (l.err ? 'err' : 'cmd');
    return '<div class="line"><span class="prompt">$</span><span class="'+cls+'">'+escapeHtml(l.text)+'</span></div>';
  }).join('');
}
async function runAIAgent(){
  const prompt = document.getElementById('ai-agent-prompt')?.value?.trim();
  if (!prompt) return notify('에이전트 지시를 입력하세요','warn');
  if (!state.project?.id) return notify('프로젝트가 없습니다','error');
  const btn = document.getElementById('ai-agent-btn');
  const loading = document.getElementById('ai-loading');
  btn.disabled = true;
  loading?.classList.remove('hidden');
  appendAgentMsg('user', prompt);
  document.getElementById('ai-agent-prompt').value = '';
  const provider = document.getElementById('ai-provider')?.value || 'cobuddy';
  const model = document.getElementById('ai-model')?.value || 'cobuddy';
  const r = await api.post('/api/ai/agent', {
    project_id: state.project.id,
    prompt,
    current_file: state.currentFile,
    provider,
    model,
    history: window._aiAgentHistory,
  });
  loading?.classList.add('hidden');
  btn.disabled = false;
  if (r.error) {
    appendAgentMsg('assistant', '오류: ' + r.error);
    notify(r.error, 'error');
    return;
  }
  const applied = r.applied || [];
  appendAgentMsg('assistant', r.message || (applied.length ? '작업 완료' : '변경 없음'));
  if (applied.length) {
    window._aiAgentHistory.push({role:'user', content: prompt});
    window._aiAgentHistory.push({role:'assistant', content: r.message || ''});
  }
  const termLines = applied.map(a=>({text: a.type + ' ' + a.path, ok: true}));
  (r.errors || []).forEach(e=> termLines.push({text: (e.path ? e.path + ': ' : '') + (e.error || 'error'), err: true}));
  if (!termLines.length) termLines.push({text: 'no file changes', err: !applied.length});
  logAgentTerminal(termLines);
  if (applied.length && r.files) {
    state.files = r.files;
    const ft = document.getElementById('file-tree');
    if (ft) ft.innerHTML = renderFileTree();
    for (const act of applied) {
      if (act.path && act.path === state.currentFile && state.editor) {
        state.applyingRemoteChange = true;
        state.editor.setValue(r.files[act.path] ?? '');
        state.applyingRemoteChange = false;
        break;
      }
    }
    notify(`${applied.length}개 파일 업데이트됨`, 'success');
  } else if (!applied.length) {
    notify('파일이 변경되지 않았습니다', 'warn');
  }
}
function switchAITab(tab){
  document.querySelectorAll('.ai-tab').forEach(el=>el.classList.remove('active'));
  document.querySelectorAll('.ai-tab-content').forEach(el=>el.classList.add('hidden'));
  document.querySelector(`.ai-tab[onclick="switchAITab('${tab}')"]`)?.classList.add('active');
  document.getElementById(`ai-tab-${tab}`)?.classList.remove('hidden');
}

function setAIPrompt(text){
  document.getElementById('ai-prompt').value = text;
  document.getElementById('ai-prompt').focus();
}
function setAgentPrompt(text){
  const el = document.getElementById('ai-agent-prompt');
  if (el) { el.value = text; el.focus(); }
}

async function askAI(mode='generate'){
  const loading = document.getElementById('ai-loading');
  const result = document.getElementById('ai-result');
  
  if (mode === 'generate') {
    const prompt = document.getElementById('ai-prompt')?.value?.trim();
    if (!prompt) return notify('프롬프트를 입력하세요','warn');
    
    loading.classList.remove('hidden');
    result.classList.add('hidden');
    
    const provider = document.getElementById('ai-provider')?.value || 'cobuddy';
    const model = document.getElementById('ai-model')?.value || 'cobuddy';
    const r = await api.post('/api/ai', {
      prompt,
      code: state.currentFile && state.files[state.currentFile] ? state.files[state.currentFile] : '',
      language: state.currentFile ? fileLang(state.currentFile) : 'javascript',
      provider,
      model
    });
    
    loading.classList.add('hidden');
    if (r.error) { notify(r.error, 'error'); return; }
    
    const metadata = document.getElementById('ai-metadata');
    if (r.provider || r.model) {
      metadata.textContent = `공급자: ${r.provider||'OpenAI'} · 모델: ${r.model||'gpt-5.5-pro'}`;
      metadata.classList.remove('hidden');
    } else metadata.classList.add('hidden');
    
    document.getElementById('ai-explanation').textContent = r.explanation || '✨ AI가 생성한 코드';
    document.getElementById('ai-code').textContent = r.code || '';
    result.classList.remove('hidden');
    window._aiCode = r.code;
  }
  else if (mode === 'analyze') {
    const code = state.editor?.getModel?.()?.getValue?.() || state.files[state.currentFile] || '';
    if (!code) return notify('분석할 코드가 없습니다','error');
    
    loading.classList.remove('hidden');
    result.classList.add('hidden');
    
    const provider = document.getElementById('ai-provider')?.value || 'cobuddy';
    const model = document.getElementById('ai-model')?.value || 'cobuddy';
    const r = await api.post('/api/ai/analyze', {
      code,
      language: fileLang(state.currentFile || 'js'),
      provider,
      model
    });
    
    loading.classList.add('hidden');
    if (r.error) { notify(r.error, 'error'); return; }
    
    result.classList.remove('hidden');
    document.getElementById('ai-explanation').textContent = '🔍 코드 분석 결과';
    document.getElementById('ai-code').textContent = '';
    
    if (r.issues && r.issues.length > 0) {
      const issuesEl = document.getElementById('ai-issues');
      issuesEl.innerHTML = `<div class="text-red-400 font-bold mb-2">⚠️ 발견된 문제:</div><ul class="text-sm list-disc list-inside">${r.issues.map(i=>`<li>${i}</li>`).join('')}</ul>`;
      issuesEl.classList.remove('hidden');
    }
    if (r.optimizations && r.optimizations.length > 0) {
      const optEl = document.getElementById('ai-optimizations');
      optEl.innerHTML = `<div class="text-yellow-400 font-bold mb-2">⚡ 최적화 제안:</div><ul class="text-sm list-disc list-inside">${r.optimizations.map(o=>`<li>${o}</li>`).join('')}</ul>`;
      optEl.classList.remove('hidden');
    }
    if (r.suggestions && r.suggestions.length > 0) {
      const sugEl = document.getElementById('ai-suggestions');
      sugEl.innerHTML = `<div class="text-cyan-400 font-bold mb-2">💡 개선 제안:</div><ul class="text-sm list-disc list-inside">${r.suggestions.map(s=>`<li>${s}</li>`).join('')}</ul>`;
      sugEl.classList.remove('hidden');
    }
  }
  else if (mode === 'explain') {
    const code = state.editor?.getModel?.()?.getValue?.() || state.files[state.currentFile] || '';
    if (!code) return notify('설명할 코드가 없습니다','error');
    
    loading.classList.remove('hidden');
    result.classList.add('hidden');
    
    const provider = document.getElementById('ai-provider')?.value || 'cobuddy';
    const model = document.getElementById('ai-model')?.value || 'cobuddy';
    const r = await api.post('/api/ai/explain', {
      code,
      language: fileLang(state.currentFile || 'js'),
      provider,
      model
    });
    
    loading.classList.add('hidden');
    if (r.error) { notify(r.error, 'error'); return; }
    
    result.classList.remove('hidden');
    document.getElementById('ai-explanation').textContent = '📖 코드 설명';
    const analysisEl = document.getElementById('ai-analysis');
    analysisEl.textContent = r.explanation || '';
    analysisEl.classList.remove('hidden');
    document.getElementById('ai-code').textContent = '';
  }
  else if (mode === 'fix') {
    const code = state.editor?.getModel?.()?.getValue?.() || state.files[state.currentFile] || '';
    if (!code) return notify('수정할 코드가 없습니다','error');
    
    const issue = document.getElementById('ai-issue')?.value?.trim();
    
    loading.classList.remove('hidden');
    result.classList.add('hidden');
    
    const provider = document.getElementById('ai-provider')?.value || 'cobuddy';
    const model = document.getElementById('ai-model')?.value || 'cobuddy';
    const r = await api.post('/api/ai/fix', {
      code,
      issue,
      language: fileLang(state.currentFile || 'js'),
      provider,
      model
    });
    
    loading.classList.add('hidden');
    if (r.error) { notify(r.error, 'error'); return; }
    
    document.getElementById('ai-explanation').textContent = r.explanation || '🐛 수정된 코드';
    document.getElementById('ai-code').textContent = r.code || '';
    result.classList.remove('hidden');
    window._aiCode = r.code;
  }
}

function insertAICode(){
  if (!window._aiCode || !state.editor) return notify('생성된 코드가 없습니다','error');
  const pos = state.editor.getPosition();
  state.editor.executeEdits('',[{range:new monaco.Range(pos.lineNumber,pos.column,pos.lineNumber,pos.column),text:'\n'+window._aiCode+'\n'}]);
  notify('코드 삽입 완료!','success');
}

function copyAICode(){
  if (!window._aiCode) return notify('코드가 없습니다','error');
  navigator.clipboard.writeText(window._aiCode).then(()=>notify('클립보드 복사 완료!','success'));
}

// ==================== 실시간 협업 ====================
function setupSocket(pid){
  if (state.socket) state.socket.disconnect();
  state.socket = io();
  state.socket.emit('join_project', {pid, user: state.user?.username||'guest'});
  
  state.socket.on('online_users', users=>{
    state.onlineUsers = users;
    refreshPresenceUI();
  });
  
  state.socket.on('editing_status', status=>{
    state.editingStatus = status;
    const ft = document.getElementById('file-tree');
    if (ft) ft.innerHTML = renderFileTree();
    refreshPresenceUI();
  });
  
  state.socket.on('cursor_update', d=>{
    if (!state.collaborators[d.user]) {
      const idx = Object.keys(state.collaborators).length % 5;
      state.collaborators[d.user] = {colorIdx: idx};
    }
    Object.assign(state.collaborators[d.user], d);
    const online = state.onlineUsers.find(o=>o.user===d.user);
    if (online) Object.assign(online, d);
    refreshPresenceUI();
    if (d.file === state.currentFile) redrawCollabCursors();
  });
  
  state.socket.on('new_chat', msg=>{
    state.chat.push(msg);
    const cl = document.getElementById('chat-list');
    if (cl) { cl.innerHTML = renderChat(); cl.scrollTop = cl.scrollHeight; }
  });
  
  state.socket.on('code_updated', d=>{
    if (!state.collaborators[d.user]) {
      const idx = Object.keys(state.collaborators).length % 5;
      state.collaborators[d.user] = {colorIdx: idx};
    }
    Object.assign(state.collaborators[d.user], {
      file: d.file,
      editingLine: d.editingLine || d.changes?.[0]?.startLine,
      line: d.editingLine || d.changes?.[0]?.startLine,
      col: 1,
    });
    if (d.file === state.currentFile && state.editor) {
      // 외부 변경 표시는 modifiedLines에 추가하지 않음 (다른 사람의 작업)
      applyRemoteFileContent(d.file, d.content);
      redrawCollabCursors();
    }
    state.files[d.file] = d.content;
  });
  
  state.socket.on('file_renamed', d=>{
    if (state.files[d.old] !== undefined) {
      state.files[d.new] = state.files[d.old];
      delete state.files[d.old];
      state.openTabs = state.openTabs.map(t=>t===d.old?d.new:t);
      if (state.currentFile===d.old) state.currentFile = d.new;
      notify(`${d.user}님이 파일 이름을 변경: ${d.new}`,'info');
      render(); setTimeout(initEditor,50);
    }
  });
  
  state.socket.on('file_deleted', d=>{
    if (state.files[d.path] !== undefined) {
      delete state.files[d.path];
      state.openTabs = state.openTabs.filter(t=>t!==d.path);
      if (state.currentFile===d.path) state.currentFile = state.openTabs[0]||null;
      notify(`${d.user||'누군가'}님이 파일 삭제: ${d.path}`,'warn');
      render(); if (state.currentFile) setTimeout(initEditor,50);
    }
  });

  state.socket.on('file_updated', d=>{
    applyRemoteFileContent(d.path, d.content);
    if (d.user !== state.user?.username) notify(`${d.user}님이 ${d.path.split('/').pop()} 파일을 저장했습니다`,'info');
  });

  state.socket.on('member_added', d=>{
    if (!state.project?.members) return;
    state.project.members[d.username] = d.role;
    notify(`${d.username}님이 프로젝트에 추가되었습니다`,'info');
    render(); if (state.currentFile) setTimeout(initEditor,50);
  });

  state.socket.on('member_role_updated', d=>{
    if (!state.project?.members) return;
    state.project.members[d.username] = d.role;
    state.project.member_permissions = state.project.member_permissions || {};
    if (d.permissions) state.project.member_permissions[d.username] = d.permissions;
    notify(`${d.username} 권한이 ${d.role}(으)로 변경되었습니다`,'info');
    render(); if (state.currentFile) setTimeout(initEditor,50);
  });
}

function redrawCollabCursors(){
  if (!state.editor) return;
  if (!state.settings || state.settings.showCollaborators === false) return;
  
  const decos = [];
  Object.values(state.collabWidgets || {}).forEach(w=>state.editor.removeContentWidget(w));
  state.collabWidgets = {};
  Object.entries(state.collaborators).forEach(([user, info])=>{
    if (user === state.user?.username) return;
    if (info.file !== state.currentFile) return;
    if (!info.line) return;
    
    const colorIdx = info.colorIdx;
    const line = info.line || info.editingLine;
    const col = info.col || 1;
    if (info.editingLine) {
      decos.push({
        range: new monaco.Range(info.editingLine,1,info.editingLine,1),
        options: { isWholeLine: true, className: 'collab-editing-line-'+colorIdx }
      });
    }
    // 커서
    decos.push({
      range: new monaco.Range(line, col, line, col),
      options: {
        className: 'collab-cursor-'+colorIdx,
        hoverMessage: {value: '**'+user+'**'},
        stickiness: monaco.editor.TrackedRangeStickiness.NeverGrowsWhenTypingAtEdges,
      }
    });
    // 선택영역
    if (info.selection) {
      decos.push({
        range: new monaco.Range(
          info.selection.startLine, info.selection.startCol,
          info.selection.endLine, info.selection.endCol
        ),
        options: { className: 'collab-selection-'+colorIdx }
      });
    }
    const node = document.createElement('div');
    node.className = 'collab-name-tag';
    node.style.background = COLLAB_COLORS[colorIdx];
    node.textContent = user;
    const widget = {
      getId: ()=>`collab.name.${user}`,
      getDomNode: ()=>node,
      getPosition: ()=>({
        position: {lineNumber: line, column: col},
        preference: [monaco.editor.ContentWidgetPositionPreference.ABOVE],
      }),
    };
    state.collabWidgets[user] = widget;
    state.editor.addContentWidget(widget);
  });
  
  state._collabDecoIds = state.editor.deltaDecorations(state._collabDecoIds||[], decos);
}

function sendChat(){
  const inp = document.getElementById('chat-input');
  if (!inp.value.trim()) return;
  state.socket.emit('send_chat', {pid: state.project.id, user: state.user?.username, msg: inp.value});
  inp.value = '';
}

// ==================== 친구 ====================
function renderFriends(){
  if (!state.user) { location.hash='login'; return ''; }
  return `
  ${topNav()}
  <div class="flex">${sidebar('friends')}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <div class="flex items-center justify-between gap-4 flex-wrap mb-6">
        <div>
          <h1 class="text-3xl font-bold">친구</h1>
          <p class="text-gray-400 mt-1">온라인 여부를 확인하고 프로젝트에서 바로 초대할 수 있습니다.</p>
        </div>
        <div class="flex gap-2 w-full sm:w-auto">
          <input id="friend-username" class="input" placeholder="친구 아이디" onkeydown="if(event.key==='Enter')addFriendFromPage()">
          <button class="mc-btn" onclick="addFriendFromPage()"><i class="fas fa-user-plus"></i> 친추</button>
        </div>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        ${state.friends.map(f=>`
          <div class="card p-5">
            <div class="flex items-center gap-3">
              <span class="online-dot ${f.online?'':'opacity-30'}" style="${f.online?'background:#00ff88;box-shadow:0 0 8px #00ff88':''}"></span>
              <div class="w-12 h-12 rounded-full flex items-center justify-center text-2xl"
                   style="background:linear-gradient(135deg,#00d4ff,#a855f7)">${f.avatar||'👤'}</div>
              <div class="flex-1 min-w-0">
                <a href="#profile/${f.username}" class="font-bold hover:text-cyan-400">${f.username}</a>
                <div class="text-xs ${f.online?'text-green-400':'text-gray-500'}">${f.online?'온라인':'오프라인'}${f.mutual?' · 맞친':''}</div>
              </div>
            </div>
            <p class="text-sm text-gray-400 mt-3 line-clamp-2">${f.bio || '소개가 없습니다'}</p>
            <div class="flex gap-2 mt-4">
              <a href="#profile/${f.username}" class="neon-btn flex-1 justify-center">프로필</a>
              <button class="neon-btn pink" onclick="removeFriend('${f.username}')">삭제</button>
            </div>
          </div>
        `).join('') || '<div class="text-gray-500">친구가 없습니다. 아이디로 친구를 추가해보세요.</div>'}
      </div>
    </main>
  </div>`;
}

async function addFriendFromPage(){
  const inp = document.getElementById('friend-username');
  const username = inp.value.trim();
  if (!username) return notify('친구 아이디를 입력해주세요','warn');
  const r = await api.post('/api/friends/add', {username});
  if (r.ok) { notify('친구 추가 완료','success'); navigate('friends'); }
  else notify(r.error || '친구 추가 실패','error');
}

async function removeFriend(username){
  const r = await api.del(`/api/friends/${username}`);
  if (r.ok) { notify('친구 삭제 완료','success'); navigate('friends'); }
  else notify(r.error || '삭제 실패','error');
}

// ==================== 프로필 ====================
function renderProfile(){
  const u = state.viewedUser;
  if (!u) return `${topNav()}<div class="p-8 text-center text-gray-400">사용자를 찾을 수 없습니다</div>`;
  const isMe = state.user?.username === u.username;
  const isFollowing = state.user && u.followers.includes(state.user.username);
  return `
  ${topNav()}
  <div class="flex">${sidebar('profile')}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <div class="card p-8 mb-6">
        <div class="flex items-center gap-6 flex-wrap">
          <div class="w-24 h-24 rounded-full flex items-center justify-center text-5xl"
               style="background:linear-gradient(135deg,#00d4ff,#a855f7)">${u.avatar}</div>
          <div class="flex-1 min-w-0">
            <h1 class="text-3xl font-bold">${u.username}</h1>
            <div class="text-xs mt-1 ${u.online?'text-green-400':'text-gray-500'}"><span class="online-dot ${u.online?'':'opacity-30'}" style="${u.online?'background:#00ff88;box-shadow:0 0 8px #00ff88':''}"></span> ${u.online?'온라인':'오프라인'}</div>
            <p class="text-gray-400 mt-1">${u.bio || '소개가 없습니다'}</p>
            <div class="flex gap-4 mt-3 text-sm">
              <span><strong>${u.projects.length}</strong> 프로젝트</span>
              <span><strong>${u.followers.length}</strong> 팔로워</span>
              <span><strong>${u.following.length}</strong> 팔로잉</span>
            </div>
          </div>
          ${!isMe && state.user ? `
            <button class="${isFollowing?'neon-btn':'mc-btn'}" onclick="doFollow('${u.username}')">
              ${isFollowing?'친구':'친구 추가'}
            </button>
          ` : isMe ? `<a href="#settings" class="neon-btn">설정</a>` : ''}
        </div>
      </div>
      <h2 class="text-xl font-bold mb-4">프로젝트</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        ${u.projects.map(p=>`
          <a href="#project/${p.id}" class="card p-5 block">
            <div class="text-4xl mb-2">${p.thumbnail}</div>
            <div class="font-bold mb-2">${p.name}</div>
            <div class="flex gap-3 text-xs text-gray-400">
              <span><i class="fas fa-heart text-pink-400"></i> ${p.likes}</span>
              <span><i class="fas fa-download text-cyan-400"></i> ${p.downloads}</span>
            </div>
          </a>
        `).join('') || '<div class="text-gray-500">아직 프로젝트가 없습니다</div>'}
      </div>
    </main>
  </div>`;
}
async function doFollow(username){
  const r = await api.post(`/api/users/${username}/follow`);
  notify(r.following?'친구 추가 완료':'친구 삭제 완료','success');
  navigate('profile/'+username);
}

// ==================== 설정 (확장) ====================
function renderSettings(){
  if (!state.user) { location.hash='login'; return ''; }
  const s = state.settings || {};
  return `
  ${topNav()}
  <div class="flex">${sidebar('settings')}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <h1 class="text-3xl font-bold mb-8">⚙️ 설정</h1>
      <div class="max-w-3xl space-y-6">
        
        <!-- 프로필 -->
        <div class="card p-6">
          <h2 class="text-lg font-bold mb-4"><i class="fas fa-user text-cyan-400"></i> 프로필</h2>
          <div class="space-y-3">
            <div><label class="text-sm text-gray-400 block mb-1">아바타 이모지</label>
              <input id="set-avatar" class="input" value="${state.user.avatar}"></div>
            <div><label class="text-sm text-gray-400 block mb-1">자기소개</label>
              <textarea id="set-bio" class="input" rows="3">${state.user.bio||''}</textarea></div>
            <div><label class="text-sm text-gray-400 block mb-1">이메일</label>
              <input id="set-email" class="input" value="${state.user.email||''}"></div>
          </div>
          <button class="mc-btn mt-4" onclick="saveProfile()">프로필 저장</button>
        </div>

        <!-- 에디터 -->
        <div class="card p-6">
          <h2 class="text-lg font-bold mb-4"><i class="fas fa-code text-purple-400"></i> 에디터</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label class="text-sm text-gray-400 block mb-1">테마</label>
              <select class="input" data-setting="theme">
                <option value="minescript" ${s.theme==='minescript'?'selected':''}>MineScript Dark</option>
                <option value="vs-dark" ${s.theme==='vs-dark'?'selected':''}>VS Dark</option>
                <option value="minescript-light" ${s.theme==='minescript-light'?'selected':''}>Light</option>
                <option value="minescript-hc" ${s.theme==='minescript-hc'?'selected':''}>고대비</option>
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-400 block mb-1">폰트</label>
              <select class="input" data-setting="fontFamily">
                <option value="JetBrains Mono">JetBrains Mono</option>
                <option value="Fira Code" ${s.fontFamily==='Fira Code'?'selected':''}>Fira Code</option>
                <option value="Consolas" ${s.fontFamily==='Consolas'?'selected':''}>Consolas</option>
                <option value="monospace" ${s.fontFamily==='monospace'?'selected':''}>monospace</option>
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-400 block mb-1">폰트 크기 (${s.fontSize||14}px)</label>
              <input type="range" min="10" max="24" value="${s.fontSize||14}" class="w-full" data-setting="fontSize" oninput="this.previousElementSibling.textContent='폰트 크기 ('+this.value+'px)'">
            </div>
            <div>
              <label class="text-sm text-gray-400 block mb-1">탭 크기</label>
              <select class="input" data-setting="tabSize">
                <option value="2" ${(s.tabSize||2)==2?'selected':''}>2</option>
                <option value="4" ${s.tabSize==4?'selected':''}>4</option>
                <option value="8" ${s.tabSize==8?'selected':''}>8</option>
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-400 block mb-1">커서 스타일</label>
              <select class="input" data-setting="cursorStyle">
                <option value="line" ${s.cursorStyle==='line'?'selected':''}>Line</option>
                <option value="block" ${s.cursorStyle==='block'?'selected':''}>Block</option>
                <option value="underline" ${s.cursorStyle==='underline'?'selected':''}>Underline</option>
              </select>
            </div>
            <div>
              <label class="text-sm text-gray-400 block mb-1">커서 깜빡임</label>
              <select class="input" data-setting="cursorBlinking">
                <option value="smooth" ${s.cursorBlinking==='smooth'?'selected':''}>Smooth</option>
                <option value="blink" ${s.cursorBlinking==='blink'?'selected':''}>Blink</option>
                <option value="solid" ${s.cursorBlinking==='solid'?'selected':''}>Solid</option>
                <option value="phase" ${s.cursorBlinking==='phase'?'selected':''}>Phase</option>
              </select>
            </div>
          </div>
          <div class="mt-5 space-y-3">
            ${[
              {k:'minimap',l:'미니맵 표시', dv:true},
              {k:'wordWrap',l:'줄 바꿈', dv:false},
              {k:'lineNumbers',l:'줄 번호', dv:true},
              {k:'autoSave',l:'자동 저장', dv:true},
              {k:'autoComplete',l:'자동 완성', dv:true},
              {k:'formatOnPaste',l:'붙여넣기 시 자동 포맷', dv:true},
              {k:'showCollaborators',l:'협업자 커서 표시', dv:true},
              {k:'showEditingIndicator',l:'편집 중 표시', dv:true},
            ].map(opt=>{
              const cur = s[opt.k] !== undefined ? s[opt.k] : opt.dv;
              return `<label class="flex items-center justify-between">
                <span class="text-sm">${opt.l}</span>
                <label class="switch">
                  <input type="checkbox" data-setting="${opt.k}" ${cur?'checked':''}>
                  <span class="slider"></span>
                </label>
              </label>`;
            }).join('')}
          </div>
          <div class="mt-4">
            <label class="text-sm text-gray-400 block mb-1">자동 저장 지연 (${s.autoSaveDelay||1000}ms)</label>
            <input type="range" min="300" max="5000" step="100" value="${s.autoSaveDelay||1000}" class="w-full" data-setting="autoSaveDelay" oninput="this.previousElementSibling.textContent='자동 저장 지연 ('+this.value+'ms)'">
          </div>
          <button class="mc-btn mt-5" onclick="saveSettings()">설정 저장</button>
        </div>

        <!-- 연동 -->
        <div class="card p-6">
          <h2 class="text-lg font-bold mb-4"><i class="fas fa-link text-green-400"></i> 연동 계정</h2>
          <div class="space-y-2">
            <button class="neon-btn w-full text-left" onclick="notify('준비중','info')"><i class="fab fa-github mr-2"></i> GitHub 연동</button>
            <button class="neon-btn purple w-full text-left" onclick="notify('준비중','info')"><i class="fab fa-discord mr-2"></i> Discord 연동</button>
          </div>
        </div>

        <!-- 위험 -->
        <div class="card p-6" style="border-color:#ff3b8b">
          <h2 class="text-lg font-bold mb-4 text-pink-400">위험 구역</h2>
          <button class="neon-btn pink" onclick="doLogout()">로그아웃</button>
        </div>
      </div>
    </main>
  </div>`;
}

async function saveProfile(){
  const r = await api.post('/api/profile', {
    avatar: document.getElementById('set-avatar').value,
    bio: document.getElementById('set-bio').value,
    email: document.getElementById('set-email').value,
  });
  if (r.ok){ notify('프로필 저장됨','success'); init(); }
}

async function saveSettings(){
  const newSettings = {};
  document.querySelectorAll('[data-setting]').forEach(el=>{
    const k = el.dataset.setting;
    let v;
    if (el.type === 'checkbox') v = el.checked;
    else if (el.type === 'range' || el.type === 'number') v = parseInt(el.value);
    else v = el.value;
    newSettings[k] = v;
  });
  const r = await api.post('/api/settings', newSettings);
  if (r.ok){ 
    state.settings = r.settings;
    notify('설정 저장됨','success'); 
  }
}

async function doLogout(){
  await api.post('/api/logout');
  if (state.presenceSocket) { state.presenceSocket.disconnect(); state.presenceSocket = null; }
  state.user = null;
  notify('로그아웃됨','success');
  location.hash='home'; init();
}

// ==================== 도구 ====================
function renderTools(){
  return `
  ${topNav()}
  <div class="flex">${sidebar('tools')}
    <main id="main-content" class="flex-1 p-4 md:p-8 fade-in">
      <h1 class="text-3xl font-bold mb-2">🔧 생성기 도구</h1>
      <p class="text-gray-400 mb-8">GUI로 손쉽게 Minecraft JSON 파일 생성</p>
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        ${[
          {i:'🗡',t:'Item 생성기',d:'커스텀 아이템'},
          {i:'🧟',t:'Entity 생성기',d:'몹/엔티티'},
          {i:'📜',t:'Recipe 생성기',d:'조합 레시피'},
          {i:'🎬',t:'Animation 생성기',d:'애니메이션'},
          {i:'📋',t:'Manifest 생성기',d:'manifest.json'},
          {i:'🧩',t:'Component 생성기',d:'엔티티 컴포넌트'},
        ].map(t=>`
          <div class="card p-6 cursor-pointer" onclick="notify('AI 어시스턴트에서 자연어로 요청해 보세요!','info')">
            <div class="text-5xl mb-3">${t.i}</div>
            <div class="font-bold mb-1">${t.t}</div>
            <div class="text-sm text-gray-400">${t.d}</div>
          </div>
        `).join('')}
      </div>
    </main>
  </div>`;
}

function attachHandlers(){
  // 템플릿 선택 효과
  document.querySelectorAll('input[name=template]').forEach(r=>{
    r.addEventListener('change', e=>{
      document.querySelectorAll('#template-grid label').forEach(l=>l.style.borderColor='');
      e.target.closest('label').style.borderColor='var(--neon)';
    });
    if (r.checked) r.dispatchEvent(new Event('change'));
  });

  // 업로드 드래그앤드롭
  const dz = document.getElementById('drop-zone');
  if (dz) {
    dz.ondragover = e=>{e.preventDefault();dz.classList.add('drag-over')};
    dz.ondragleave = ()=>dz.classList.remove('drag-over');
    dz.ondrop = e=>{
      e.preventDefault(); dz.classList.remove('drag-over');
      if (e.dataTransfer.files[0]) {
        document.getElementById('pack-file').files = e.dataTransfer.files;
        onFileSelected();
      }
    };
  }
}

init();
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return INDEX_HTML

if __name__ == '__main__':
    print("="*60)
    print("🎮 MineScript Hub 실행 중...")
    print("📍 http://localhost:5000")
    print("👤 데모 계정: demo / demo")
    print("="*60)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)

