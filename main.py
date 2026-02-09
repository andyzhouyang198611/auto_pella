import os
import sys
import time
import re
import platform
import requests
from datetime import datetime, timedelta, timezone
from seleniumbase import SB
from pyvirtualdisplay import Display

# ================= 配置区域 =================
ENV_VAR_NAME = "PELLA_BATCH"
LOGIN_URL = "https://www.pella.app/login"
SERVER_URL_TEMPLATE = "https://www.pella.app/server/{server_id}"

# ================= 辅助函数 =================
def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        return display
    return None

def mask_email(email):
    if "@" not in email: return email
    name, domain = email.split("@")
    if len(name) > 3:
        return f"{name[:2]}***{name[-1]}@{domain}"
    return f"{name[:1]}***@{domain}"

def get_beijing_time():
    utc_now = datetime.now(timezone.utc)
    bj_now = utc_now + timedelta(hours=8)
    return bj_now.strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(token, chat_id, message):
    if not token or not chat_id: return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram 发送失败: {e}")

# ================= 核心逻辑 =================
def run_pella_task(account_line):
    os.makedirs("screenshots", exist_ok=True)
    parts = [p.strip() for p in account_line.split(",")]
    if len(parts) < 3: return
    email, password, server_id, tg_token, tg_chat_id = parts[0], parts[1], parts[2], parts[3], parts[4]

    log = {"account": mask_email(email), "ip": "Unknown", "status": "Unknown", "expiry": "Unknown", "renew_status": "", "logs": []}
    print(f"🚀 开始调试账号: {log['account']}")

    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # --- 1. 登录 (保持原有逻辑) ---
            print("👉 登录中...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            try: sb.uc_gui_click_captcha(); sb.sleep(2)
            except: pass
            
            sb.type('input[name="identifier"]', email + "\n")
            sb.sleep(5)
            
            if not sb.is_element_visible('input[name="password"]'):
                if sb.is_element_visible('button:contains("Continue")'): sb.uc_click('button:contains("Continue")')
            sb.wait_for_element('input[name="password"]', timeout=15)
            sb.type('input[name="password"]', password + "\n")
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # --- 2. 进入服务器 ---
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            sb.open(target_url)
            sb.sleep(8)

            # ==========================================
            # 🔍 核心调试区域：打印页面所有按钮信息
            # ==========================================
            print("\n" + "="*30)
            print("🔍 开始扫描页面元素...")
            
            # 1. 打印所有 button 标签的文本
            buttons = sb.find_elements("button")
            print(f"📄 页面上共找到 {len(buttons)} 个 <button> 标签:")
            for i, btn in enumerate(buttons):
                try:
                    txt = btn.text.replace("\n", " ").strip()
                    html = btn.get_attribute("outerHTML")[:100] # 只打印前100个字符避免刷屏
                    print(f"   [{i}] Text='{txt}' | HTML={html}...")
                except: pass
            print("="*30 + "\n")

            # 2. 强力寻找 START
            # 策略：不限标签，只要包含 START 且可见
            print("👉 正在寻找 'START'...")
            
            # 尝试多种选择器
            potential_starts = []
            
            # 方案A: 包含文字的按钮
            potential_starts += sb.find_elements("button:contains('START')")
            # 方案B: 包含文字的任意元素 (div/span/a)
            potential_starts += sb.find_elements("//*[contains(text(),'START')]")
            # 方案C: 你的截图显示是绿色的，尝试找绿色按钮 (Tailwind css)
            potential_starts += sb.find_elements("button.bg-green-500")
            potential_starts += sb.find_elements("button.bg-emerald-500")

            start_btn_found = None
            
            # 过滤并去重
            unique_starts = []
            for el in potential_starts:
                if el not in unique_starts: unique_starts.append(el)

            if unique_starts:
                print(f"🎯 找到了 {len(unique_starts)} 个疑似 START 的元素!")
                
                for idx, el in enumerate(unique_starts):
                    try:
                        # 获取信息
                        tag = el.tag_name
                        txt = el.text
                        print(f"   👉 尝试点击第 {idx+1} 个候选者: <{tag}> '{txt}'")
                        
                        # 高亮显示（方便截图查看）
                        sb.execute_script("arguments[0].style.border='5px solid red';", el)
                        sb.save_screenshot(f"screenshots/debug_highlight_{idx}.png")
                        
                        # 强力点击
                        sb.execute_script("arguments[0].click();", el)
                        sb.sleep(3)
                        
                        # 检查是否有反应 (Check Console text)
                        logs = sb.get_text("body")[-500:] # 获取页面最后500字符通常是控制台
                        if "Starting" in logs or "Booting" in logs:
                            print("✅ 触发了启动日志！")
                            log["status"] = "已触发启动"
                            log["logs"].append("调试模式启动成功")
                            break
                    except Exception as e:
                        print(f"   ❌ 点击失败: {e}")
            else:
                print("⚠️ 全网搜索未找到包含 'START' 的元素！")
                log["logs"].append("未找到START按钮")
                sb.save_screenshot("screenshots/debug_no_start.png")

            # 3. 检查状态
            sb.sleep(5)
            if sb.is_element_visible("button:contains('STOP')"):
                 log["status"] = "运行中"
            elif sb.is_element_visible("button:contains('START')"):
                 log["status"] = "已停止 (启动可能失败)"
            else:
                 log["status"] = "未知"

            # 4. 获取时间和IP (保持不变)
            try:
                txt = sb.get_text("body")
                match = re.search(r"expires in\s+([0-9D\sHM]+)", txt)
                log["expiry"] = match.group(1).strip() if match else "Error"
            except: pass
            
            # 5. 续期 (简单点击)
            btns = sb.find_elements("button:contains('Claim')")
            cnt = 0
            for b in btns:
                if "Claimed" not in b.text:
                    sb.execute_script("arguments[0].click();", b)
                    cnt += 1
            if cnt > 0: log["renew_status"] = f"调试续期 {cnt}"
            else: log["renew_status"] = "无需续期"

        except Exception as e:
            print(f"❌ 错误: {e}")
            log["logs"].append(f"Err: {str(e)[:30]}")
            sb.save_screenshot("screenshots/debug_crash.png")
        finally:
            send_report(log, tg_token, tg_chat_id)

def send_report(log, token, chat_id):
    msg = f"""
<b>🛠 Pella 调试报告</b>
🆔 账号: <code>{log['account']}</code>
📊 状态: <b>{log['status']}</b>
⏳ 剩余: {log['expiry']}
📝 日志: {' | '.join(log['logs'])}
"""
    send_telegram(token, chat_id, msg)

if __name__ == "__main__":
    batch = os.getenv(ENV_VAR_NAME)
    if batch:
        display = setup_xvfb()
        for line in batch.strip().splitlines():
            if line.strip() and not line.startswith("#"):
                run_pella_task(line)
        if display: display.stop()
