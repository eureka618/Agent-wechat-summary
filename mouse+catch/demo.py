import os
import time
import pyautogui
import pytesseract
from PIL import Image
from openai import OpenAI

# ==================== 配置 ====================
# DeepSeek API 配置
DEEPSEEK_API_KEY = "sk-279e265cebc943eeaee25b73bcc57c5a"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Tesseract 路径（请根据你的实际安装路径修改）
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# 安全设置
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.8

# ==================== 第一部分：用API获取问答 ====================
print("=" * 60)
print("第一部分：通过 DeepSeek API 获取答案")
print("=" * 60)

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL
)

question = "复旦大学的校训是什么？"

try:
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "user", "content": question}
        ],
        temperature=0.7,
        max_tokens=200
    )
    
    answer = response.choices[0].message.content
    
    print(f"\n我的问题: {question}")
    print(f"\nDeepSeek回答: {answer}")
    print("\nAPI调用成功！")
    
except Exception as e:
    print(f"API调用失败: {e}")
    print("将使用备用文本继续视觉演示...")
    question = "复旦大学的校训是什么？"
    answer = "备用答案：由于API调用失败，这里显示备用文本。复旦大学的校训是'博学而笃志，切问而近思'。"

# ==================== 第二部分：视觉识别演示 ====================
print("\n" + "=" * 60)
print("第二部分：视觉识别演示（截屏 + OCR）")
print("=" * 60)

# 创建一个临时区域来展示问答内容
# 这里我们截取当前整个屏幕，然后用OCR识别这段文字

print("\n正在截取屏幕...")
time.sleep(1)

# 截取整个屏幕
screenshot = pyautogui.screenshot()
screenshot_path = "deepseek_screenshot.png"
screenshot.save(screenshot_path)
print(f"屏幕截图已保存: {screenshot_path}")

# 用OCR识别截图
print("\n正在进行OCR识别...")
try:
    img = Image.open(screenshot_path)
    ocr_text = pytesseract.image_to_string(img, lang='chi_sim+eng')
    
    print("\n" + "=" * 60)
    print("OCR识别结果（屏幕上的文字）:")
    print("=" * 60)
    print(ocr_text[:800])  # 显示前800个字符
    print("=" * 60)
    
except Exception as e:
    print(f"OCR识别失败: {e}")
    ocr_text = "OCR识别失败，请检查Tesseract是否正确安装"

# ==================== 第三部分：保存完整结果 ====================
print("\n" + "=" * 60)
print("第三部分：保存结果到文件")
print("=" * 60)

result_text = f"""
========== DeepSeek API 问答结果 ==========

我的问题: {question}

DeepSeek回答:
{answer}

========== 屏幕OCR识别结果 ==========

{ocr_text}

========== 生成时间 ==========
{time.strftime("%Y-%m-%d %H:%M:%S")}
"""

output_file = "deepseek_result.txt"
with open(output_file, "w", encoding="utf-8") as f:
    f.write(result_text)

print(f"\n完整结果已保存到: {output_file}")
print("\n任务完成！")