# -*- coding: utf-8 -*-
"""WorkBuddy Buddy加油站 每日自动签到

用法:
  python checkin.py            # 无头执行签到(需已登录)
  python checkin.py --login    # 打开浏览器手动登录一次, 登录态存到 state.json
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = "https://www.workbuddy.cn"
HERE = pathlib.Path(__file__).parent
STATE = HERE / "state.json"
STATUS_URL = "/billing/meter/checkin-status"
CLAIM_URL = "/billing/meter/daily-checkin"

FETCH_JS = """async ([path]) => {
  const r = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
    body: '{}'
  });
  const t = await r.text();
  try { return {http: r.status, body: JSON.parse(t)}; }
  catch { return {http: r.status, raw: t.slice(0, 300)}; }
}"""


# def call(page, path):
#     try:
#         return page.evaluate(FETCH_JS, [path])
#     except Exception as e:
#         return {"http": -1, "error": str(e)[:200]}

def call(page, path, retry_401=True):
    try:
        ret = page.evaluate(FETCH_JS, [path])
    except Exception as e:
        return {"http": -1, "error": str(e)[:200]}
    # HTTP 401 时重试一次：先重新进入首页，给浏览器一次刷新登录态/cookie的机会
    if retry_401 and ret.get("http") == 401:
        try:
            print(f"[WARN] {path} 返回 HTTP 401，准备重试一次 ...")
            page.goto(BASE, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            return page.evaluate(FETCH_JS, [path])
        except Exception as e:
            return {"http": -1, "error": str(e)[:200]}
    return ret


def main():
    login_mode = "--login" in sys.argv
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    with sync_playwright() as p:
        if login_mode:
            browser = p.chromium.launch(headless=False, args=["--start-maximized"])
            ctx = browser.new_context(no_viewport=True)
        else:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(storage_state=str(STATE) if STATE.exists() else None)
        page = ctx.new_page()
        try:
            if login_mode:
                page.bring_to_front()
        except Exception:
            pass
        try:
            page.goto(BASE, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"[FAIL] 打开页面失败: {e}")
            browser.close()
            return 1

        if login_mode:
            print(">>> 浏览器已打开, 请在里面登录 WorkBuddy（微信扫码或手机号）")
            print(">>> 登录成功后会自动检测并保存, 无需其他操作 ...")
            for _ in range(150):
                page.wait_for_timeout(4000)
                # st = call(page, STATUS_URL)
                st = call(page, STATUS_URL, retry_401=False)
                if st.get("http") == 200:
                    ctx.storage_state(path=str(STATE))
                    print("[OK] 检测到登录成功, 登录态已保存到 state.json!")
                    page.wait_for_timeout(1000)
                    browser.close()
                    return 0
            print("[FAIL] 等待超时(10分钟), 请重试")
            browser.close()
            return 2

        st = call(page, STATUS_URL)
        code = st.get("http")
        if code in (401, 302) or code == -1:
            print(f"[FAIL] 登录态失效 (HTTP {code}), 请运行: python checkin.py --login")
            browser.close()
            return 2

        body = st.get("body") or {}
        data = body.get("data") or {}
        if isinstance(data, dict):
            print(f"[状态] 本期主题: {data.get('theme_name') or '未开始'} | "
                  f"今日已签: {data.get('today_checked_in')} | 连签: {data.get('streak_days')}天 | "
                  f"累计积分: {data.get('total_credits')}")
        if isinstance(data, dict) and data.get("today_checked_in"):
            print("[OK] 今日已签到, 无需重复领取")
            browser.close()
            return 0

        res = call(page, CLAIM_URL)
        rb = res.get("body") or {}
        code, msg = rb.get("code"), rb.get("msg", "")
        print(f"[领取] code={code} msg={msg}")
        if code == 0:
            print(f"[DONE] 签到成功! {json.dumps(rb.get('data') or {}, ensure_ascii=False)[:200]}")
            rc = 0
        elif code == 10001 or "已签到" in msg:
            print("[OK] 今日已签到(服务端确认)")
            rc = 0
        else:
            print(f"[WARN] 领取未成功, 请人工核对")
            rc = 3
        # 刷新登录态(服务端可能轮换 cookie)
        try:
            ctx.storage_state(path=str(STATE))
        except Exception:
            pass
        browser.close()
        return rc


if __name__ == "__main__":
    sys.exit(main())
