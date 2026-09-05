import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://gold.tanaka.co.jp/commodity/souba/index.php"
STATE_FILE = "state.json"
JST = ZoneInfo("Asia/Tokyo")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "avg": 23282.0,
            "cur": 24918.0,
            "bb": 24413.0,
            "last_alert_key": ""
        }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def yen_value(text):
    text = text.replace(",", "").replace("円", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    return int(float(text))


def get_public_prices():
    r = requests.get(
        URL,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 gold-monitor/1.7"}
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    for row in soup.find_all("tr"):
        cells = [
            c.get_text(" ", strip=True)
            for c in row.find_all(["th", "td"])
        ]

        if not cells or cells[0].strip() != "金":
            continue

        # 前日比（+135円/-135円）を除外し、
        # 正の価格（25,282円 / 24,733円）だけを取得する。
        price_cells = []
        for cell in cells[1:]:
            value = cell.strip()
            if re.fullmatch(r"\d[\d,]*(?:\.\d+)?\s*円", value):
                price_cells.append(value)

        if len(price_cells) >= 2:
            retail = yen_value(price_cells[0])
            buyback = yen_value(price_cells[1])

            if (
                retail is not None
                and buyback is not None
                and retail >= 10000
                and buyback >= 10000
            ):
                return retail, buyback

        # 予備処理
        row_text = " ".join(cells)
        values = re.findall(
            r"(?<![+\-])\d[\d,]*(?:\.\d+)?\s*円",
            row_text
        )

        clean = []
        for value in values:
            n = yen_value(value)
            if n is not None and n >= 10000:
                clean.append(n)

        if len(clean) >= 2:
            return clean[0], clean[1]

    raise RuntimeError(
        "田中貴金属の金の公開小売価格・公開買取価格を確認できませんでした"
    )


def decision(cur, avg):
    if cur >= avg:
        return 0, "購入なし"

    drop = (avg - cur) / avg * 100

    if drop < 3:
        return 1, "1〜3%下落"
    if drop < 5:
        return 2, "3〜5%下落"
    if drop < 7:
        return 3, "5〜7%下落"
    if drop < 10:
        return 4, "7〜10%下落"

    return 0, "10%以上下落・手入力"


def line_push(message):
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.getenv("LINE_USER_ID")

    if not token or not user_id:
        print("LINE Secrets未設定。通知はスキップしました。")
        return

    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={
            "to": user_id,
            "messages": [{"type": "text", "text": message}]
        },
        timeout=20
    )
    r.raise_for_status()


def main():
    now = datetime.now(JST)
    retail, buyback = get_public_prices()
    state = load_state()

    qty, reason = decision(retail, state["avg"])
    drop = (
        ((state["avg"] - retail) / state["avg"] * 100)
        if state["avg"]
        else 0
    )

    state["cur"] = retail
    state["bb"] = buyback

    alert_key = f"{now.date()}-{now:%H:%M}-{retail}-{qty}"

    if qty > 0 and alert_key != state.get("last_alert_key"):
        line_push(
            "【田中貴金属 金監視 V1.7】\n"
            f"確認時刻: {now:%Y-%m-%d %H:%M} JST\n"
            f"公開小売価格: {retail:,}円/g\n"
            f"公開買取価格: {buyback:,}円/g\n"
            f"平均購入金額: {state['avg']:,.0f}円/g\n"
            f"下落率: {drop:.2f}%\n"
            f"購入判定: {qty}g\n"
            f"理由: {reason}\n"
            "※ネット取引価格は使用していません。\n"
            "※実注文OFF"
        )
        state["last_alert_key"] = alert_key

    save_state(state)

    print(json.dumps({
        "checked_at_jst": now.isoformat(),
        "public_retail": retail,
        "public_buyback": buyback,
        "average": state["avg"],
        "drop_pct": round(drop, 2),
        "decision_g": qty,
        "reason": reason
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
