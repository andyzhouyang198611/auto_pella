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
    if len(parts) < 3:
        print(f"❌ 账号格式错误: {account_line}")
        return

    email, password, server_id = parts[0], parts[1], parts[2]
    tg_token = parts[3] if len(parts) > 3 else None
    tg_chat_id = parts[4] if len(parts) > 4 else None

    log_info = {
        "account": mask_email(email), "ip": "Unknown", "status": "Unknown",
        "expiry": "Unknown", "actions": [], "hint": ""
    }

    print(f"🚀 开始处理账号: {log_info['account']}")

    with SB(uc=True, test=True, locale="en") as sb:
        try:
            # 1. 打开登录页
            print("👉 打开登录页面...")
            sb.uc_open_with_reconnect(LOGIN_URL, 6)
            
            # 自动处理验证码 (如果出现)
            try: sb.uc_gui_click_captcha(); sb.sleep(2)
            except: pass

            # --- 步骤 1: 输入邮箱 ---
            print("👉 寻找邮箱输入框...")
            email_selectors = ['input[placeholder*="email address"]', 'input[name="identifier"]', 'input[type="email"]']
            
            email_input = None
            for sel in email_selectors:
                if sb.is_element_visible(sel):
                    email_input = sel
                    break
            
            if not email_input:
                sb.save_screenshot(f"screenshots/err_no_email.png")
                raise Exception("找不到邮箱输入框")
            
            print(f"👉 输入邮箱并回车: {email}")
            # ⭐ 修改点：输入邮箱后直接加 \n (回车)，模拟用户按 Enter 键提交
            sb.type(email_input, email + "\n")
            sb.sleep(3) # 等待回车生效

            # --- 步骤 2: 确认是否跳转到密码页 ---
            print("👉 检查是否需要输入密码...")
            
            # 定义密码框可能的选择器
            pwd_selectors = ['input[type="password"]', 'input[name="password"]']
            pwd_found = False

            # 循环检查 5 次 (共15秒)
            for i in range(5):
                # 1. 检查密码框
                for pwd_sel in pwd_selectors:
                    if sb.is_element_visible(pwd_sel):
                        print("✅ 密码框已出现")
                        pwd_found = True
                        break
                if pwd_found: break

                # 2. 检查验证码 (Turnstile iframe)
                if sb.is_element_visible('iframe[src*="challenges"]'):
                    print("⚠️ 遇到验证码，尝试点击...")
                    sb.uc_gui_click_captcha()
                    sb.sleep(3)

                # 3. 还在邮箱页？尝试点击 Continue 按钮补救
                if sb.is_element_visible('button:contains("Continue")'):
                    print(f"⚠️ 页面未跳转 (第{i+1}次)，尝试点击 Continue 按钮...")
                    try:
                        # 使用 UC 模式的点击，更像真人
                        sb.uc_click('button:contains("Continue")') 
                    except:
                        sb.click('button:contains("Continue")')
                
                sb.sleep(3)

            if not pwd_found:
                raise Exception("无法进入密码输入界面 (卡在邮箱页或验证码)")

            # --- 步骤 3: 输入密码 ---
            print("👉 输入密码...")
            # 再次确认具体的密码框选择器
            final_pwd_sel = 'input[name="password"]'
            if not sb.is_element_visible(final_pwd_sel):
                final_pwd_sel = 'input[type="password"]'
            
            sb.type(final_pwd_sel, password + "\n") # 同样使用回车提交
            sb.sleep(5)
            
            # 如果回车没登录，尝试点击登录按钮
            if sb.is_element_visible('button:contains("Continue")'):
                 print("👉 点击 Continue 登录...")
                 sb.uc_click('button:contains("Continue")')

            # --- 步骤 4: 等待登录成功 ---
            print("👉 等待进入主页...")
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # 2. 直达服务器详情页
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            print(f"👉 进入服务器页面: {target_url}")
            sb.open(target_url)
            sb.sleep(8) 

            # 获取 IP
            try:
                txt = sb.get_text("body")
                ip = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', txt)
                log_info["ip"] = ip.group(0) if ip else f"ID: {server_id[:8]}..."
            except: pass

            # 检查状态
            if sb.is_element_visible('button:contains("START")'):
                print("⚠️ 启动服务器...")
                sb.click('button:contains("START")')
                log_info["actions"].append("已执行启动")
                sb.sleep(5)
                log_info["status"] = "启动中"
            elif sb.is_element_visible('button:contains("STOP")'):
                print("✅ 运行中")
                log_info["status"] = "运行中"
            else:
                log_info["status"] = "未知"

            # 获取时间
            try:
                txt = sb.get_text("body")
                match = re.search(r"expires in\s+([^\.]+)\.", txt, re.IGNORECASE)
                log_info["expiry"] = match.group(1).strip() if match else "未找到"
            except: log_info["expiry"] = "Error"
            
            if "D" in log_info["expiry"]: log_info["hint"] = "剩余 > 24小时"
            else: log_info["hint"] = "⚠️ 剩余 < 24小时"

            # 续期
            print("👉 检查续期...")
            btns = sb.find_elements('button:contains("Claim")')
            cnt = 0
            for btn in btns:
                try:
                    if "Claimed" not in btn.text:
                        print(f"👉 点击: {btn.text}")
                        btn.click()
                        cnt += 1
                        sb.sleep(3)
                except: pass
            
            if cnt > 0: log_info["actions"].append(f"续期 {cnt} 次")
            if not log_info["actions"]: log_info["actions"].append("无操作")

        except Exception as e:
            print(f"❌ 错误: {e}")
            log_info["status"] = "出错"
            log_info["actions"].append(f"Err: {str(e)[:40]}")
            # 截图
            ts = int(time.time())
            sname = f"screenshots/err_{email.split('@')[0]}_{ts}.png"
            sb.save_screenshot(sname)
            print(f"📸 截图: {sname}")
        
        finally:
            send_report(log_info, tg_token, tg_chat_id)

def send_report(info, token, chat_id):
    action_str = " | ".join(info["actions"])
    emoji = "⚠️" if "启动" in action_str else ("🎉" if "续期" in action_str else "ℹ️")
    if "Err" in action_str: emoji = "❌"
    
    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{info['account']}</code>
🖥 IP: <code>{info['ip']}</code>
⏰ 时间: {get_beijing_time()}

{emoji} <b>{action_str}</b>
📊 状态: {info['status']}
⏳ 剩余: <b>{info['expiry']}</b>
💡 提示: {info['hint']}
"""
    send_telegram(token, chat_id, msg)

if __name__ == "__main__":
    batch = os.getenv(ENV_VAR_NAME)
    if not batch: sys.exit(1)
    
    display = setup_xvfb()
    for line in batch.strip().splitlines():
        if line.strip() and not line.startswith("#"):
            run_pella_task(line)
            time.sleep(5)
    if display: display.stop()
