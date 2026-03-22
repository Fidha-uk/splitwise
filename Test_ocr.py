"""
Run this in your splitwise folder:  python test_ocr.py
It will tell you exactly what's missing.
"""
import sys, os

print("=" * 50)
print("SplitWise OCR Diagnostic")
print("=" * 50)

# 1. Check Python version
print(f"\n✅ Python: {sys.version}")

# 2. Check pytesseract
try:
    import pytesseract
    print("✅ pytesseract: installed")
except ImportError:
    print("❌ pytesseract: NOT installed")
    print("   Fix: pip install pytesseract")

# 3. Check Pillow
try:
    from PIL import Image
    print("✅ Pillow: installed")
except ImportError:
    print("❌ Pillow: NOT installed")
    print("   Fix: pip install pillow")

# 4. Check Tesseract binary
try:
    import pytesseract
    ver = pytesseract.get_tesseract_version()
    print(f"✅ Tesseract binary: found (version {ver})")
except Exception as e:
    print(f"❌ Tesseract binary: NOT found")
    print(f"   Error: {e}")
    print("   Fix: Download from https://github.com/UB-Mannheim/tesseract/wiki")
    print("   Install to: C:\\Program Files\\Tesseract-OCR\\")
    # Try to auto-set path
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\lenovo\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
    ]
    for p in common_paths:
        if os.path.exists(p):
            print(f"\n   ✅ FOUND at: {p}")
            print(f"   Add this line to your app.py after imports:")
            print(f'   pytesseract.pytesseract.tesseract_cmd = r"{p}"')
            break

# 5. Check python-dotenv
try:
    import dotenv
    print("✅ python-dotenv: installed")
except ImportError:
    print("❌ python-dotenv: NOT installed")
    print("   Fix: pip install python-dotenv")

# 6. Check .env file
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    print(f"✅ .env file: found")
else:
    print(f"❌ .env file: NOT found at {env_path}")

print("\n" + "=" * 50)
print("Done! Share the output above if still stuck.")
print("=" * 50)