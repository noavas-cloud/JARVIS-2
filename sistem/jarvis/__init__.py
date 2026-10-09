"""JARVIS 2 — kişisel macOS asistanının yeniden yazılmış çekirdeği.

Katmanlar:
  paths    → klasörler ve kimlik (JARVIS 2'ye özgü; asıl JARVIS ile karışmaz)
  state    → çekirdekle arayüz arasındaki tek paylaşılan durum (arayüz her karede okur)
  audio    → mikrofon, ses düzeyi dengeleme, hoparlör
  tools    → araç kaydı: bildirim + işleyici tek yerde (masaüstü, telefon köprüsü ve Telegram aynı kaydı kullanır)
  prompt   → sistem talimatı (yalnız kullanılabilir araçların kuralları eklenir)
  live     → Gemini Live oturumu (yeniden bağlanma, oturum devamı, araç çağrıları)
  ui/      → Tk arayüzü; küre ui/orb.py (asıl JARVIS'in küresiyle birebir aynı)
"""

VERSION = "2.0.0"
