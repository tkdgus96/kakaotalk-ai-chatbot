from pathlib import Path

from app.boss.utils.price import format_mesos_kr


def render_settlement_html(settlement, drops):
    tpl = Path("app/templates/settlement_view.html").read_text(encoding="utf-8")
    drop_items = "".join(
        f"<li>{d['item_name']} - {format_mesos_kr(d['price_mesos'])}</li>" for d in drops
    )
    status = "완료" if settlement["status"] == "DONE" else "미완료"
    values = {
        "{{boss_name}}": settlement["boss_name"],
        "{{status}}": status,
        "{{total}}": format_mesos_kr(settlement["total_price_mesos"]),
        "{{member_count}}": str(settlement["member_count"]),
        "{{per_member}}": format_mesos_kr(settlement["price_per_member_mesos"]),
        "{{created_at}}": settlement["created_at"],
        "{{completed_at}}": settlement["completed_at"] or "-",
        "{{drop_items}}": drop_items,
    }
    for k, v in values.items():
        tpl = tpl.replace(k, v)
    return tpl
