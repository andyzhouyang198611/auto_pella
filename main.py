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
    """Linux下启动虚拟显示"""
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
    # 确保截图目录存在
    os.makedirs("screenshots", exist_ok=True)

    parts = [p.strip() for p in account_line.split(",")]
    if len(parts) < 3:
        print(f"❌ 账号格式错误: {account_line}")
        return

    email, password, server_id = parts[0], parts[1], parts[2]
    tg_token = parts[3] if len(parts) > 3 else None
    tg_chat_id = parts[4] if len(parts) > 4 else None

    log_info = {
        "account": mask_email(email),
        "ip": "Unknown",
        "status": "Unknown",
        "expiry": "Unknown",
        "actions": [],
        "hint": ""
    }

    print(f"🚀 开始处理账号: {log_info['account']}")

    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # 1. 打开登录页
            print("👉 打开登录页面...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            
            # 尝试过盾
            try: sb.uc_gui_click_captcha(); sb.sleep(2)
            except: pass

            # --- 步骤 1: 输入邮箱 ---
            print("👉 等待邮箱输入框...")
            email_selectors = [
                'input[placeholder*="email address"]',
                'input[name="identifier"]',
                'input[type="email"]'
            ]
            
            email_input = None
            for sel in email_selectors:
                if sb.is_element_visible(sel):
                    email_input = sel
                    break
            
            if not email_input:
                # 截图并报错
                sb.save_screenshot(f"screenshots/error_no_email_input.png")
                raise Exception("未找到邮箱输入框")
            
            print(f"👉 发现输入框 ({email_input})，输入邮箱...")
            sb.type(email_input, email)
            sb.sleep(1)
            
            print("👉 点击 Continue...")
            # 尝试点击，如果使用的是 form 提交，有时候需要点击 type=submit
            continue_btn = 'button:contains("Continue")'
            sb.click(continue_btn)
            
            # --- 关键修复: 等待密码框或错误提示 ---
            print("👉 等待跳转 (检查密码框或验证码)...")
            
            # 循环检查 5 次 (共10秒)，看是否卡在这一步
            for i in range(5):
                sb.sleep(2)
                # 1. 检查密码框出来了没
                if sb.is_element_visible('input[type="password"]'):
                    break
                
                # 2. 检查是否有 Turnstile 验证码挡路
                if sb.is_element_visible('iframe[src*="challenges"]'):
                    print("⚠️ 检测到验证码，尝试点击...")
                    sb.uc_gui_click_captcha()
                
                # 3. 检查是否还在邮箱页 (可能点击没生效)
                if sb.is_element_visible(continue_btn):
                    print(f"⚠️ 仍在邮箱页 (第 {i+1} 次尝试)，重试点击 Continue...")
                    sb.click(continue_btn)

            # --- 步骤 2: 输入密码 ---
            print("👉 确认密码输入框可见...")
            sb.wait_for_element('input[type="password"]', timeout=15)
            
            print("👉 输入密码...")
            sb.type('input[type="password"]', password)
            sb.sleep(1)
            
            print("👉 点击 Continue 登录...")
            sb.click('button:contains("Continue")')
            
            # --- 步骤 3: 等待登录完成 ---
            print("👉 等待跳转主页...")
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # 2. 直达服务器详情页
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            print(f"👉 进入服务器页面: {target_url}")
            sb.open(target_url)
            sb.sleep(8) 

            # 获取 IP
            try:
                body_text = sb.get_text("body")
                ip_match = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', body_text)
                log_info["ip"] = ip_match.group(0) if ip_match else f"ID: {server_id[:8]}..."
            except: pass

            # 检查状态
            if sb.is_element_visible('button:contains("START")'):
                print("⚠️ 服务器停止，正在启动...")
                sb.click('button:contains("START")')
                log_info["actions"].append("已执行启动")
                sb.sleep(5)
                log_info["status"] = "启动中"
            elif sb.is_element_visible('button:contains("STOP")'):
                print("✅ 服务器运行中")
                log_info["status"] = "运行中"
            else:
                log_info["status"] = "未知"

            # 获取时间
            try:
                full_text = sb.get_text("body")
                match = re.search(r"expires in\s+([^\.]+)\.", full_text, re.IGNORECASE)
                log_info["expiry"] = match.group(1).strip() if match else "未找到时间"
            except:
                log_info["expiry"] = "获取失败"
            
            if "D" in log_info["expiry"] or "Day" in log_info["expiry"]:
                 log_info["hint"] = "剩余 > 24小时"
            else:
                 log_info["hint"] = "⚠️ 剩余 < 24小时"

            # 续期操作
            print("👉 检查续期按钮...")
            btns = sb.find_elements('button:contains("Claim")')
            clicked = 0
            for btn in btns:
                try:
                    if "Claimed" not in btn.text:
                        print(f"👉 点击: {btn.text}")
                        btn.click()
                        clicked += 1
                        sb.sleep(3)
                except: pass
            
            if clicked > 0: log_info["actions"].append(f"续期 {clicked} 次")
            if not log_info["actions"]: log_info["actions"].append("无操作")

        except Exception as e:
            print(f"❌ 错误: {e}")
            log_info["status"] = "执行出错"
            log_info["actions"].append(f"Err: {str(e)[:40]}")
            
            # ======= 📸 截图保存逻辑 =======
            ts = int(time.time())
            safe_email = email.split('@')[0]
            screen_path = f"screenshots/error_{safe_email}_{ts}.png"
            try:
                sb.save_screenshot(screen_path)
                print(f"📸 错误截图已保存: {screen_path}")
            except Exception as s_e:
                print(f"⚠️ 截图保存失败: {s_e}")
            # ==============================
        
        finally:
            send_report(log_info, tg_token, tg_chat_id)

def send_report(info, token, chat_id):
    action_str = " | ".join(info["actions"])
    header_emoji = "⚠️" if "启动" in action_str else ("🎉" if "续期" in action_str else "ℹ️")
    if "Err" in action_str: header_emoji = "❌"
    
    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{info['account']}</code>
🖥 IP: <code>{info['ip']}</code>
⏰ 时间: {get_beijing_time()}

{header_emoji} <b>{action_str}</b>
📊 状态: {info['status']}
⏳ 剩余: <b>{info['expiry']}</b>
💡 提示: {info['hint']}
"""
    send_telegram(token, chat_id, msg)

if __name__ == "__main__":
    batch_data = os.getenv(ENV_VAR_NAME)
    if not batch_data: sys.exit(1)
    
    display = setup_xvfb()
    for line in batch_data.strip().splitlines():
        if line.strip() and not line.startswith("#"):
            run_pella_task(line)
            time.sleep(5)
    if display: display.stop()
