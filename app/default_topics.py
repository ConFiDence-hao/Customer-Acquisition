"""Install the agreed business topics once, as editable personal subscriptions."""
from . import db


PRESETS = (
    ('combustion', '智慧焚烧与燃烧优化',
     ['智慧焚烧', 'AI智能燃烧', '智能燃烧控制', '智能优化控制', 'APC', '锅炉优化', '炉膛火焰图像'],
     '关注垃圾焚烧的燃烧优化、智能控制、锅炉优化和炉膛火焰图像应用'),
    ('emissions', '烟气净化与排放提标',
     ['烟气超低排放', '烟气提标改造', '烟气净化', 'SCR脱硝', 'NOx提标'],
     '关注垃圾焚烧的烟气净化、排放提标和脱硝改造'),
    ('automation', '自动化控制与智慧管控',
     ['DCS组态', '智慧管控'],
     '关注垃圾焚烧的自动化控制系统、DCS组态和智慧管控应用'),
    ('fly-ash', '飞灰处置', ['飞灰处置'],
     '关注垃圾焚烧飞灰处置相关项目与资讯'),
)


def ensure_for_user(connection, user_id):
    """Use the caller's transaction; a durable marker respects later edits/deletion."""
    timestamp = db.now()
    created = False
    for preset_id, name, keywords, offering in PRESETS:
        if connection.execute(
            'SELECT 1 FROM subscription_defaults WHERE user_id=? AND preset_id=?',
            (user_id, preset_id),
        ).fetchone():
            continue
        existing = connection.execute(
            "SELECT id FROM subscriptions WHERE user_id=? AND trim(json_extract(config,'$.name'))=? ORDER BY deleted,id LIMIT 1",
            (user_id, name),
        ).fetchone()
        if existing:
            topic_id = existing['id']
        else:
            topic = {
                'name': name, 'industry': '垃圾焚烧发电', 'offering': offering,
                'keywords': keywords, 'exclude_keywords': [], 'provinces': [],
                'stages': [], 'source_ids': [], 'enabled': True,
            }
            topic_id = connection.execute(
                'INSERT INTO subscriptions(user_id,config,enabled,created_at,updated_at) VALUES(?,?,1,?,?)',
                (user_id, db.dumps(topic), timestamp, timestamp),
            ).lastrowid
            created = True
        connection.execute(
            'INSERT INTO subscription_defaults(user_id,preset_id,subscription_id,created_at) VALUES(?,?,?,?)',
            (user_id, preset_id, topic_id, timestamp),
        )
    if created:
        from .workbench import request_subscription_check
        request_subscription_check(connection, user_id)
