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
# 环境变量格式: email,password,server_id,tg_token,tg_chat_id
# 多个账号换行
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
    """脱敏邮箱"""
    if "@" not in email: return email
    name, domain = email.split("@")
    if len(name) > 3:
        return f"{name[:2]}***{name[-1]}@{domain}"
    return f"{name[:1]}***@{domain}"

def get_beijing_time():
    """获取北京时间字符串"""
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
            # 1. 登录 (分两步)
            print("👉 打开登录页面...")
            sb.uc_open_with_reconnect(LOGIN_URL, 5)
            
            # 输入邮箱 -> Continue
            print("👉 输入邮箱...")
            sb.type('input[type="email"]', email)
            sb.click('button:contains("Continue")')
            
            # 等待跳转到 factor-one 并出现密码框
            sb.wait_for_element('input[type="password"]', timeout=15)
            
            # 输入密码 -> Continue
            print("👉 输入密码...")
            sb.type('input[type="password"]', password)
            sb.click('button:contains("Continue")')
            
            # 等待登录成功 (Dashboard)
            sb.wait_for_element('a[href*="/server/"]', timeout=30)
            print("✅ 登录成功")

            # 2. 直达服务器详情页
            target_url = SERVER_URL_TEMPLATE.format(server_id=server_id)
            print(f"👉 进入服务器页面: {target_url}")
            sb.open(target_url)
            sb.sleep(5) # 等待动态加载

            # 3. 获取 IP (尝试在页面寻找 IP 格式文本)
            try:
                # 假设页面有显示IP，尝试抓取，如果没有则用 ID 代替
                body_text = sb.get_text("body")
                ip_match = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', body_text)
                if ip_match:
                    log_info["ip"] = ip_match.group(0)
                else:
                    log_info["ip"] = f"ID: {server_id[:8]}..."
            except:
                pass

            # 4. 检查 Start/Stop 状态
            if sb.is_element_visible('button:contains("START")'):
                print("⚠️ 检测到服务器停止，正在启动...")
                sb.click('button:contains("START")')
                log_info["actions"].append("已执行启动")
                sb.sleep(3)
                log_info["status"] = "启动中 (Starting)"
            elif sb.is_element_visible('button:contains("STOP")'):
                print("✅ 服务器运行中")
                log_info["status"] = "运行中 (Running)"
            else:
                log_info["status"] = "未知状态"

            # 5. 获取剩余时间 (抓取 Start/Stop 按钮附近的文字)
            # Pella 通常显示格式: "Your server expires in 1D 15H 30M."
            try:
                # 寻找包含 "expires in" 的 div 或 span
                expiry_text_full = sb.get_text_content("body")
                match = re.search(r"expires in\s+([\d\w\s]+)\.", expiry_text_full)
                if match:
                    log_info["expiry"] = match.group(1).strip()
                else:
                    # 备用方案：查找特定元素
                    log_info["expiry"] = sb.get_text(".text-muted") # 假设类名
            except:
                log_info["expiry"] = "获取失败"
            
            # 设置提示信息
            if "D" in log_info["expiry"] or "Day" in log_info["expiry"]:
                 log_info["hint"] = "剩余 > 24小时"
            else:
                 log_info["hint"] = "⚠️ 注意: 剩余时间不足 24 小时"

            # 6. 处理续期 (Claim)
            # 查找所有包含 "Claim" 的按钮
            claim_buttons = sb.find_elements('button:contains("Claim")')
            clicked_count = 0
            
            if not claim_buttons:
                log_info["actions"].append("未找到续期按钮")
            
            for btn in claim_buttons:
                txt = btn.text
                if "Claimed" in txt:
                    continue # 已经领过了
                
                # 点击领取
                print(f"👉 点击续期按钮: {txt}")
                btn.click()
                clicked_count += 1
                sb.sleep(2)
            
            if clicked_count > 0:
                log_info["actions"].append(f"成功续期 {clicked_count} 次")
            else:
                log_info["actions"].append("无需续期 (已满)")

        except Exception as e:
            print(f"❌ 发生错误: {e}")
            log_info["status"] = "脚本执行出错"
            log_info["actions"].append(str(e))
        
        finally:
            # 发送 TG 通知
            send_report(log_info, tg_token, tg_chat_id)

def send_report(info, token, chat_id):
    """
    仿照要求的格式发送通知:
    🎮 Pella 续期通知
    🆔 账号: xm***15
    🖥 IP: 85.131.251.209
    ⏰ 时间: 2026-02-09 17:49:04
    
    ℹ️ [操作结果]
    📅 状态: [Running/Stopped]
    ⏳ 剩余: 77時間27分
    💡 提示: 剩余 > 24小时
    """
    
    action_str = " | ".join(info["actions"]) if info["actions"] else "无需操作"
    if "已执行启动" in action_str:
        header_emoji = "⚠️"
        action_summary = "执行了启动操作"
    elif "成功续期" in action_str:
        header_emoji = "🎉"
        action_summary = "成功续期时长"
    else:
        header_emoji = "ℹ️"
        action_summary = "无需续期/保活"

    msg = f"""
<b>🎮 Pella 续期通知</b>
🆔 账号: <code>{info['account']}</code>
🖥 IP: <code>{info['ip']}</code>
⏰ 时间: {get_beijing_time()}

{header_emoji} <b>{action_summary}</b>
📊 状态: {info['status']}
⏳ 剩余: <b>{info['expiry']}</b>
💡 提示: {info['hint']}
"""
    print("📤 发送通知中...")
    send_telegram(token, chat_id, msg)

# ================= 主程序入口 =================
if __name__ == "__main__":
    batch_data = os.getenv(ENV_VAR_NAME)
    if not batch_data:
        print(f"❌ 未找到环境变量 {ENV_VAR_NAME}")
        sys.exit(1)
    
    display = setup_xvfb()
    
    lines = batch_data.strip().splitlines()
    for line in lines:
        if not line.strip() or line.startswith("#"): continue
        run_pella_task(line)
        time.sleep(5) # 账号间缓冲
        
    if display:
        display.stop()
