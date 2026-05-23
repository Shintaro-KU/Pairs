from flask import Flask, render_template, request, jsonify, session
import anthropic
import os
import json
import uuid
import hashlib
from pathlib import Path
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pairs-helper-secret-2024')

DATA_DIR = Path(os.environ.get('DATA_DIR', str(Path(__file__).parent / 'data')))
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── helpers ──────────────────────────────────────────────

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def load_users():
    f = DATA_DIR / 'users.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}

def save_users(u):
    (DATA_DIR / 'users.json').write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding='utf-8')

def user_dir(username):
    d = DATA_DIR / username
    d.mkdir(exist_ok=True)
    (d / 'girls').mkdir(exist_ok=True)
    return d

def load_profile(username):
    f = user_dir(username) / 'profile.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else {'hobbies': '', 'notes': '', 'style': '', 'api_key': ''}

def save_profile(username, p):
    (user_dir(username) / 'profile.json').write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding='utf-8')

def list_girls(username):
    gdir = user_dir(username) / 'girls'
    girls = []
    for f in sorted(gdir.glob('*.json'), key=lambda x: x.stat().st_mtime, reverse=True):
        girls.append(json.loads(f.read_text(encoding='utf-8')))
    return girls

def load_girl(username, gid):
    f = user_dir(username) / 'girls' / f'{gid}.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else None

def save_girl(username, g):
    (user_dir(username) / 'girls' / f'{g["id"]}.json').write_text(
        json.dumps(g, ensure_ascii=False, indent=2), encoding='utf-8')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'ログインが必要です'}), 401
        return f(*args, **kwargs)
    return decorated

def get_api_key(data, username):
    return (data.get('api_key', '').strip()
            or load_profile(username).get('api_key', '')
            or os.environ.get('ANTHROPIC_API_KEY', ''))


# ── auth ─────────────────────────────────────────────────

@app.route('/api/auth/register', methods=['POST'])
def register():
    d = request.json
    username, password = d.get('username', '').strip(), d.get('password', '').strip()
    if not username or not password:
        return jsonify({'error': 'ユーザー名とパスワードを入力してください'}), 400
    users = load_users()
    if username in users:
        return jsonify({'error': 'そのユーザー名は既に使われています'}), 400
    users[username] = hash_pw(password)
    save_users(users)
    session['username'] = username
    return jsonify({'success': True, 'username': username})

@app.route('/api/auth/login', methods=['POST'])
def login():
    d = request.json
    username, password = d.get('username', '').strip(), d.get('password', '').strip()
    users = load_users()
    if users.get(username) != hash_pw(password):
        return jsonify({'error': 'ユーザー名またはパスワードが違います'}), 401
    session['username'] = username
    return jsonify({'success': True, 'username': username})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/me')
def me():
    return jsonify({'username': session.get('username')})


# ── profile ──────────────────────────────────────────────

@app.route('/api/profile', methods=['GET', 'POST'])
@require_auth
def profile():
    u = session['username']
    if request.method == 'POST':
        save_profile(u, request.json)
        return jsonify({'success': True})
    return jsonify(load_profile(u))


# ── girls ────────────────────────────────────────────────

@app.route('/api/girls', methods=['GET', 'POST'])
@require_auth
def girls():
    u = session['username']
    if request.method == 'POST':
        d = request.json
        girl = {
            'id': str(uuid.uuid4())[:8],
            'name': d.get('name', '名前なし'),
            'profile': '',
            'goal': 'natural',
            'conversation': '',
            'notes': '',
            'created_at': datetime.now().isoformat(),
        }
        save_girl(u, girl)
        return jsonify(girl)
    return jsonify(list_girls(u))

@app.route('/api/girls/<gid>', methods=['GET', 'PUT', 'DELETE'])
@require_auth
def girl(gid):
    u = session['username']
    if request.method == 'DELETE':
        f = user_dir(u) / 'girls' / f'{gid}.json'
        if f.exists():
            f.unlink()
        return jsonify({'success': True})
    g = load_girl(u, gid)
    if not g:
        return jsonify({'error': 'Not found'}), 404
    if request.method == 'PUT':
        g.update(request.json)
        save_girl(u, g)
        return jsonify(g)
    return jsonify(g)


# ── style training ───────────────────────────────────────

@app.route('/api/style/train', methods=['POST'])
@require_auth
def train_style():
    u = session['username']
    d = request.json
    api_key = get_api_key(d, u)
    if not api_key:
        return jsonify({'error': 'APIキーが設定されていません'}), 400

    raw_text = d.get('text', '')
    images = d.get('images', [])

    extracted = []
    if images:
        client = anthropic.Anthropic(api_key=api_key)
        for img in images:
            if ',' in img:
                header, img = img.split(',', 1)
                mt = header.split(';')[0].split(':')[1] if 'data:' in header else 'image/jpeg'
            else:
                mt = 'image/jpeg'
            try:
                msg = client.messages.create(
                    model='claude-haiku-4-5-20251001',
                    max_tokens=1024,
                    messages=[{'role': 'user', 'content': [
                        {'type': 'image', 'source': {'type': 'base64', 'media_type': mt, 'data': img}},
                        {'type': 'text', 'text': 'マッチングアプリの会話スクショからメッセージを抽出。右=自分:、左=相手: 形式で。'}
                    ]}]
                )
                extracted.append(msg.content[0].text)
            except Exception:
                pass

    all_logs = (raw_text + '\n' + '\n'.join(extracted)).strip()
    if not all_logs:
        return jsonify({'error': 'ログが空です'}), 400

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=512,
        messages=[{'role': 'user', 'content': f"""以下は過去のマッチングアプリ会話ログです。
この人の返信スタイルを分析して100文字以内で特徴をまとめてください。
口調・絵文字の使い方・返信の長さ・質問の仕方などを含めて。

【ログ】
{all_logs[:5000]}"""}]
    )
    style = msg.content[0].text.strip()
    p = load_profile(u)
    p['style'] = style
    save_profile(u, p)
    return jsonify({'style': style})


# ── extract from screenshot ──────────────────────────────

@app.route('/api/extract', methods=['POST'])
@require_auth
def extract():
    u = session['username']
    d = request.json
    api_key = get_api_key(d, u)
    if not api_key:
        return jsonify({'error': 'APIキーが設定されていません'}), 400

    img = d.get('image', '')
    mode = d.get('mode', 'conversation')

    if ',' in img:
        header, img = img.split(',', 1)
        mt = header.split(';')[0].split(':')[1] if 'data:' in header else 'image/jpeg'
    else:
        mt = 'image/jpeg'

    prompts = {
        'partner_profile': 'このPairsプロフィール画面から趣味タグ・年齢・居住地・自己紹介文をすべて抽出。テキストのみ出力。',
        'conversation':    'このPairs会話画面のメッセージを時系列順に抽出。右側=「自分:」左側=「相手:」形式で。メッセージのみ出力。',
        'my_profile':      'このPairsプロフィール画面から自分の趣味タグ・自己紹介文を抽出。テキストのみ出力。',
    }
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': mt, 'data': img}},
                {'type': 'text', 'text': prompts.get(mode, prompts['conversation'])}
            ]}]
        )
        return jsonify({'text': msg.content[0].text.strip()})
    except anthropic.AuthenticationError:
        return jsonify({'error': 'APIキーが無効です'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── generate reply ────────────────────────────────────────

@app.route('/api/generate', methods=['POST'])
@require_auth
def generate():
    u = session['username']
    d = request.json
    api_key = get_api_key(d, u)
    if not api_key:
        return jsonify({'error': 'APIキーが設定されていません'}), 400

    conversation   = d.get('conversation', '').strip()
    partner_profile = d.get('partner_profile', '').strip()
    goal           = d.get('goal', 'natural')
    girl_id        = d.get('girl_id')

    if girl_id:
        g = load_girl(u, girl_id)
        if g:
            g['conversation']  = d.get('conversation', g['conversation'])
            g['profile']       = partner_profile or g.get('profile', '')
            g['goal']          = goal
            save_girl(u, g)

    my = load_profile(u)
    style_info = f"\n\n【過去ログから分析した自分の返信スタイル】\n{my['style']}" if my.get('style') else ''

    goal_map = {
        'natural': '自然な会話を続ける（まだ誘わない）',
        'phone':   '電話・通話に誘う流れを作る',
        'date':    '食事やデートに誘う流れを作る',
    }

    system_prompt = f"""あなたはマッチングアプリPairsの返信を考えるプロのライターです。
ユーザーの代わりに魅力的で自然な返信案を3つ考えてください。

【ユーザーの趣味・自己紹介】
{my.get('hobbies') or '（未設定）'}

【補足メモ】
{my.get('notes') or '（なし）'}{style_info}

【返信の方針】
- 目標: {goal_map.get(goal, goal_map['natural'])}
- 口調: カジュアルで親しみやすい自然な日本語
- 文字数: 40〜120文字
- 絵文字: 0〜2個（多用しない）
- 相手の話題に乗りながら自分のことも少し伝える
- 会話が続くよう疑問や話題を1つ混ぜる
- 3案はトーンを変える（フランク・丁寧・ユーモア）

出力形式:
【案1】
（返信文）

【案2】
（返信文）

【案3】
（返信文）"""

    user_content = ""
    if partner_profile:
        user_content += f"【相手プロフィール】\n{partner_profile}\n\n"
    user_content += f"【会話の流れ（最新が一番下）】\n{conversation}\n\n返信案を3つ作ってください。"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=1024,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_content}]
        )
        return jsonify({'result': msg.content[0].text})
    except anthropic.AuthenticationError:
        return jsonify({'error': 'APIキーが無効です'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/manifest.json')
def manifest():
    from flask import Response
    import json as _json
    data = {
        "name": "Pairs アシスタント",
        "short_name": "Pairs助手",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f8f0f5",
        "theme_color": "#e0457b",
        "orientation": "portrait",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    }
    return Response(_json.dumps(data, ensure_ascii=False), mimetype='application/manifest+json')

@app.route('/sw.js')
def sw():
    from flask import Response
    js = """
const CACHE = 'pairs-assistant-v1';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.add('/')));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (new URL(e.request.url).pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(e.request)
      .then(r => { caches.open(CACHE).then(c => c.put(e.request, r.clone())); return r; })
      .catch(() => caches.match(e.request))
  );
});
""".strip()
    return Response(js, mimetype='application/javascript')

@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    print("起動中... http://localhost:5001")
    print("スマホからは http://192.168.17.182:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
