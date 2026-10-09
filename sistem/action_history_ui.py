"""On-demand action history window; database and undo work stay off Tk's thread."""
import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from actions.action_history import get_action_history, undo_action


class ActionHistoryPanel:
    def __init__(self, parent, report_result):
        self.report_result = report_result
        self.events = queue.Queue()
        self.busy = False
        self.loading = False
        self.refresh_pending = False
        self.alive = True
        self.rows = None
        self.window = tk.Toplevel(parent)
        self.window.title('JARVIS — İşlem geçmişi')
        self.window.geometry('780x560')
        self.window.minsize(620, 360)
        self.window.configure(bg='#08191d')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        header = tk.Frame(self.window, bg='#08191d')
        header.pack(fill='x', padx=16, pady=12)
        tk.Label(header, text='İŞLEM GEÇMİŞİ', bg='#08191d', fg='#d7eff4',
                 font=('Helvetica', 15, 'bold')).pack(side='left')
        self.close_button = tk.Button(header, text='Kapat', command=self.close)
        self.close_button.pack(side='right', padx=(8, 0))
        self.refresh_button = tk.Button(header, text='Yenile', command=self.refresh)
        self.refresh_button.pack(side='right')
        tk.Label(self.window, text='Geri alınabilenler: Dosya taşıma • Ad değiştirme • Takvim • Hatırlatıcı',
                 bg='#08191d', fg='#7aa8b3', wraplength=570, justify='left').pack(anchor='w', padx=16)
        self.message = tk.Label(self.window, text='Geçmiş yükleniyor…', wraplength=570,
                                justify='left', anchor='w', bg='#08191d', fg='#d6c052')
        self.message.pack(fill='x', padx=16, pady=8)
        area = tk.Frame(self.window, bg='#08191d')
        area.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        self.canvas = tk.Canvas(area, bg='#08191d', highlightthickness=0)
        scroll = tk.Scrollbar(area, command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body = tk.Frame(self.canvas, bg='#08191d')
        self.body_id = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self.body_id, width=event.width))
        self.refresh()
        self._poll_after = self.window.after(80, self._poll)

    def close(self):
        if not self.alive:
            return
        self.alive = False
        self.window.after_cancel(self._poll_after)
        self.window.destroy()

    def refresh(self):
        if not self.alive:
            return
        if self.loading or self.busy:
            self.refresh_pending = True
            return
        self.refresh_pending = False
        self.loading = True
        def load():
            try:
                result = json.loads(get_action_history(40))
            except Exception as exc:
                result = {'status': 'error', 'message': str(exc)}
            self.events.put(('history', result))
        threading.Thread(target=load, daemon=True).start()

    def request_undo(self, operation_id):
        if self.busy:
            return
        self.busy = True
        self.refresh_button.configure(state='disabled')
        self.message.configure(text='İşlem geri alınıyor…', fg='#d6c052')
        self._render(self.rows or [])
        def run():
            try:
                result = json.loads(undo_action(operation_id))
            except Exception as exc:
                result = {'status': 'error', 'message': str(exc)}
            self.events.put(('undo', result))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        # All widget changes, including results from worker threads, happen here.
        if not self.alive:
            return
        while not self.events.empty():
            kind, result = self.events.get_nowait()
            if kind == 'history':
                self.loading = False
                if result.get('status') == 'ok':
                    rows = result.get('actions', [])
                    if rows != self.rows:
                        self.rows = rows
                        self._render(rows)
                    if not rows:
                        self.message.configure(text='Henüz kayıt yok. Yeni işlemler burada görünecek.')
                    elif not self.busy:
                        self.message.configure(text='Desteklenen işlemleri yanlarındaki düğmeyle geri alabilirsin.')
                else:
                    self.message.configure(text=result.get('message', 'Geçmiş okunamadı.'), fg='#d6c052')
            else:
                self.busy = False
                self.refresh_button.configure(state='normal')
                self.message.configure(text=result.get('message', ''),
                                       fg='#5fd0f0' if result.get('status') in ('ok', 'already_done') else '#d6c052')
                self.report_result(result)
                self.refresh()
        if self.refresh_pending and not self.loading and not self.busy:
            self.refresh()
        self._poll_after = self.window.after(80, self._poll)

    def _render(self, rows):
        for child in self.body.winfo_children():
            child.destroy()
        states = {'done': 'DONE', 'undone': 'UNDONE', 'failed': 'FAILED', 'partial': 'PARTIAL',
                  'running': 'IN PROGRESS', 'no_change': 'NO CHANGE'}
        for row in rows:
            card = tk.Frame(self.body, bg='#0b2227', highlightbackground='#2f6f7d', highlightthickness=1)
            card.pack(fill='x', padx=4, pady=5)
            try:
                date = datetime.fromisoformat(row['created_at']).strftime('%d.%m %H:%M')
            except (ValueError, KeyError):
                date = ''
            title = f"{date}  ·  {row['label']}  ·  {states.get(row['state'], row['state'])}"
            tk.Label(card, text=title, bg='#0b2227', fg='#d7eff4', anchor='w',
                     justify='left', wraplength=520, font=('Helvetica', 11, 'bold')).pack(fill='x', padx=10, pady=(9, 3))
            detail = row.get('summary', '')[:900]
            if row.get('last_error'):
                detail += '\n' + row['last_error']
            tk.Label(card, text=detail, bg='#0b2227', fg='#aac3c9', anchor='w',
                     justify='left', wraplength=430).pack(side='left', fill='x', expand=True, padx=10, pady=(3, 10))
            if row.get('can_undo'):
                tk.Button(card, text='Geri al', state='disabled' if self.busy else 'normal',
                          command=lambda key=row['id']: self.request_undo(key)).pack(side='right', padx=10, pady=8)
            elif row['state'] in ('done', 'partial'):
                tk.Label(card, text='Geri alma yok', bg='#0b2227', fg='#7aa8b3').pack(side='right', padx=10)
