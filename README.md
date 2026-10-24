# Digit Academy Management System
Offline Windows desktop software (Python + PySide6 + SQLite): admissions, courses/batches, custom fields, fees & receipts (A4/thermal), attendance, reports (PDF/CSV/Excel), ID cards, users & permissions, backup/restore.

## .exe / installer banana (GitHub se)
1. GitHub par naya repository banao, is folder ki saari files upload karo.
2. `.github/workflows/build-windows.yml` agar upload na ho, to "Add file > Create new file" se wahi naam likh kar `.github/workflows/build-windows.yml` ka text paste karo.
3. Actions tab > "Build Windows installer" > Run workflow. 10-15 min baad Artifacts me `DigitAcademy_Setup` download karo.
4. Setup chalao. SmartScreen aaye to "More info > Run anyway".

## Data
- Data: `%LOCALAPPDATA%\DigitAcademy` (install/uninstall isko nahi chhoota).
- Backup: Backup & Restore page (`.dabak`), default `Documents\DigitAcademy Backups`. Aadhaar encrypted hai; key backup me hoti hai.
- Pehli baar chalane par admin account setup hota hai. Courses me fees khud set karo (koi fees pehle se nahi bhari).

## Developer
`pip install -r requirements-dev.txt`, then `python main.py`; tests: `QT_QPA_PLATFORM=offscreen pytest`.

## Limits
Windows .exe is Linux sandbox me test nahi hua (CI selftest karta hai). Daily closing sheet print hoti hai, DB me store nahi. WhatsApp reminder sirf text + wa.me link. Cloud/multi-PC sync nahi.
