#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pella.app 服务器保活与续期脚本 (单变量版)
"""

import asyncio
import os
import datetime
import requests
import re
from datetime import timezone, timedelta
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

# =====================================================================
#                          配置区域
# =====================================================================

# 强制无头模式
USE_HEADLESS = True 
WAIT_TIMEOUT = 30000 

# 从单一变量中读取所有配置
# 格式: 邮箱,密码,服务器ID,BotToken,ChatID
PELLA_CREDENTIALS = os.getenv("PELLA_CREDENTIALS")

# =====================================================================
#                        Telegram 通知类
# =====================================================================

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

    def send_pella_notify(self, email_addr, server_name, status, expiry_text, claim_status):
        if not self.enabled: return
        
        # 北京时间
        beijing_time = datetime.datetime.now(timezone(timedelta(hours=8)))
        timestamp = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 简单脱敏
        safe_email = email_addr[:2] + "***" + email_addr.split('@')[-1] if email_addr else "Unknown"

        # 构建消息
        msg = f"<b>🎮 Pella.app 续期通知</b>\n"
        msg += f"🆔 账号: <code>{safe_email}</code>\n"
        msg += f"🖥 服务器: <code>{server_name}</code>\n"
        msg += f"⏰ 时间: {timestamp}\n\n"
        
        # 状态图标
        status_icon = "🟢" if "Running" in status or "运行中" in status else "🔴"
        msg += f"{status_icon} 状态: <b>{status}</b>\n"
        
        # 剩余时间
        msg += f"⏳ 剩余: <b>{expiry_text}</b>\n"
        
        # 续期操作结果
        msg += f"🎁 续期: {claim_status}\n"
        
        # 发送
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
            print("✅ Telegram 通知已发送")
        except Exception as e:
            print(f"❌ Telegram 发送失败: {e}")

# =====================================================================
#                        Pella 自动化类
# =====================================================================

class PellaBot:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        
        # 配置信息初始化
        self.email = ""
        self.password = ""
        self.server_id = ""
        self.tg_token = ""
        self.tg_chat_id = ""
        self.notifier = None
        
        # 运行结果数据
        self.server_name = "Unknown"
        self.server_status = "Unknown"
        self.expiry_text = "Unknown"
        self.claim_log = []

    def parse_config(self):
        """解析合并的配置变量"""
        if not PELLA_CREDENTIALS:
            print("❌ 未找到环境变量 PELLA_CREDENTIALS")
            return False
            
        try:
            # 使用逗号分割，去除首尾空格
            parts = [p.strip() for p in PELLA_CREDENTIALS.split(',')]
            
            if len(parts) < 3:
                print("❌ PELLA_CREDENTIALS 格式错误，至少需要: 邮箱,密码,服务器ID")
                return False
                
            self.email = parts[0]
            self.password = parts[1]
            self.server_id = parts[2]
            
            # TG 配置是可选的
            if len(parts) >= 5:
                self.tg_token = parts[3]
                self.tg_chat_id = parts[4]
                self.notifier = TelegramNotifier(self.tg_token, self.tg_chat_id)
            else:
                print("⚠️ 未检测到完整的 Telegram 配置，将跳过推送")
                self.notifier = TelegramNotifier("", "")
                
            return True
        except Exception as e:
            print(f"❌ 解析配置失败: {e}")
            return False

    async def start(self):
        """启动浏览器"""
        p = await async_playwright().start()
        # Pella可能有反爬，使用stealth
        self.browser = await p.chromium.launch(headless=USE_HEADLESS, args=['--no-sandbox'])
        self.context = await self.browser.new_context(viewport={'width': 1920, 'height': 1080})
        self.page = await self.context.new_page()
        await stealth_async(self.page)

    async def close(self):
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()

    async def login(self):
        """登录流程"""
        try:
            print("🚀 前往登录页面...")
            await self.page.goto("https://www.pella.app/login", wait_until='networkidle')
            
            # 1. 输入邮箱
            print(f"📝 输入邮箱: {self.email}")
            await self.page.locator("input[type='email']").fill(self.email)
            # 点击 Continue (查找按钮)
            await self.page.click("button:has-text('Continue')")
            
            # 2. 等待跳转到密码页 (#/factor-one)
            # 这里稍微硬等待一下，或者等待密码框出现
            await asyncio.sleep(2)
            await self.page.wait_for_selector("input[type='password']", timeout=15000)
            
            # 3. 输入密码
            print("🔑 输入密码...")
            await self.page.locator("input[type='password']").fill(self.password)
            await self.page.click("button:has-text('Continue')")
            
            # 4. 等待登录成功 (跳转到 Dashboard)
            await self.page.wait_for_url("**/dashboard**", timeout=30000)
            print("✅ 登录成功!")
            return True
            
        except Exception as e:
            print(f"❌ 登录失败: {e}")
            return False

    async def manage_server(self):
        """管理指定服务器"""
        target_url = f"https://www.pella.app/server/{self.server_id}"
        print(f"🌐 正在进入服务器页面: {target_url}")
        
        try:
            await self.page.goto(target_url, wait_until='networkidle')
            await asyncio.sleep(3) # 等待动态内容加载
            
            # 1. 获取服务器名称
            try:
                self.server_name = await self.page.locator("h1").first.text_content()
                self.server_name = self.server_name.strip()
            except: pass

            # 2. 检查状态 (START / STOP 按钮)
            # 检查是否有 STOP 按钮 (表示正在运行)
            if await self.page.locator("button:has-text('STOP')").count() > 0:
                print("🟢 服务器正在运行 (Running)")
                self.server_status = "运行中 (Running)"
            
            # 检查是否有 START 按钮 (表示已停止)
            elif await self.page.locator("button:has-text('START')").count() > 0:
                print("🔴 服务器已停止，尝试启动...")
                await self.page.click("button:has-text('START')")
                self.server_status = "启动中 (Starting...)"
                await asyncio.sleep(2)
            else:
                self.server_status = "状态未知"

            # 3. 获取剩余时间
            # 寻找包含 "expires in" 的文本
            try:
                # 定位包含 expires in 的元素
                expiry_el = self.page.locator("text=/Your server expires in/i")
                if await expiry_el.count() > 0:
                    raw_text = await expiry_el.text_content()
                    # 提取 "1D 15H 0M" 部分
                    # 假设文本是: "Your server expires in 1D 15H 0M. You can add..."
                    match = re.search(r'expires in\s+(.*?)\.', raw_text)
                    if match:
                        self.expiry_text = match.group(1).strip()
                    else:
                        self.expiry_text = raw_text.replace("Your server expires in", "").split('.')[0].strip()
                    print(f"⏳ 剩余时间: {self.expiry_text}")
            except Exception as e:
                print(f"⚠️ 获取时间失败: {e}")

            # 4. 续期 (Claim Rewards)
            # 查找所有包含 "Claim" 的按钮
            claim_buttons = await self.page.locator("button", has_text="Claim").all()
            print(f"🎁 发现 {len(claim_buttons)} 个潜在续期按钮")
            
            action_count = 0
            for btn in claim_buttons:
                txt = await btn.text_content()
                txt = txt.strip()
                
                # 如果按钮是 "Claimed" (灰色/已领)，跳过
                if "Claimed" in txt:
                    print(f"   - 跳过: {txt}")
                    continue
                
                # 如果是 "16 HOURS Claim" 或类似，点击它
                print(f"   - 点击续期: {txt}")
                try:
                    await btn.click()
                    self.claim_log.append("✅ 点击成功")
                    action_count += 1
                    await asyncio.sleep(2) # 等待请求
                except Exception as e:
                    self.claim_log.append("❌ 点击失败")
            
            if action_count == 0:
                self.claim_log.append("无需操作 (已满或无可用)")

        except Exception as e:
            print(f"❌ 管理页面出错: {e}")
            self.server_status = "Error"

    async def run(self):
        if not self.parse_config():
            return

        try:
            await self.start()
            if await self.login():
                await self.manage_server()
        finally:
            # 发送通知
            if self.notifier:
                claim_str = ", ".join(list(set(self.claim_log))) if self.claim_log else "无操作"
                self.notifier.send_pella_notify(
                    self.email,
                    self.server_name, 
                    self.server_status, 
                    self.expiry_text, 
                    claim_str
                )
            await self.close()

if __name__ == "__main__":
    asyncio.run(PellaBot().run())
