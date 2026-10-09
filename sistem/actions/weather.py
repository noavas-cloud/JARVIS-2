"""Konuma ve tarihe göre Open-Meteo güncel hava/tahmin verisi."""
from __future__ import annotations
from datetime import date, datetime, timedelta
import os
import unicodedata
from zoneinfo import ZoneInfo
import requests


def _fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(text).lower().replace('ı','i')) if not unicodedata.combining(c))


def _condition(code):
    if code == 0: return 'açık'
    if code in (1, 2): return 'parçalı bulutlu'
    if code == 3: return 'kapalı'
    if code in (45, 48): return 'sisli'
    if code in (51, 53, 55, 56, 57): return 'çisentili'
    if code in (61, 63, 65, 66, 67): return 'yağmurlu'
    if code in (71, 73, 75, 77, 85, 86): return 'karlı'
    if code in (80, 81, 82): return 'sağanak yağışlı'
    if code in (95, 96, 99): return 'gök gürültülü sağanak yağışlı'
    return 'hava durumu ayrıntısı belirtilmemiş'


def _resolve_day(day, today):
    text = _fold(day or 'now').strip()
    if text in ('now', 'simdi', 'anlik'): return None
    if text in ('today','bugun'): return today
    if text in ('tomorrow','yarin'): return today + timedelta(days=1)
    if text in ('day_after_tomorrow','obur gun'): return today + timedelta(days=2)
    return date.fromisoformat(text)


def get_weather_summary(location: str | None = None, day: str = 'now') -> str:
    target = (location or os.environ.get('JARVIS_WEATHER_LOCATION') or 'Istanbul').strip()
    try:
        geo = requests.get('https://geocoding-api.open-meteo.com/v1/search',
                           params={'name':target,'count':5,'language':'tr'}, timeout=(4,8))
        geo.raise_for_status()
        choices = geo.json().get('results') or []
        if not choices: return f'Hata: {target} konumu bulunamadı. Şehir/ilçe adını netleştir.'
        exact = [r for r in choices if _fold(r['name']) == _fold(target)]
        place = (exact or choices)[0]
        zone = ZoneInfo(place.get('timezone') or 'UTC')
        today = datetime.now(zone).date()
        try: chosen = _resolve_day(day, today)
        except ValueError:
            return 'Hata: Hava tahmini tarihi anlaşılmadı. now, today, tomorrow veya YYYY-MM-DD kullan.'
        if chosen is not None and not today <= chosen <= today + timedelta(days=15):
            return 'Hata: Hava tahmini yalnızca bugün ve sonraki 15 gün için kullanılabilir; bu tarih için tahmin uydurma.'
        params = {'latitude':place['latitude'],'longitude':place['longitude'],'timezone':place.get('timezone','auto'),
                  'temperature_unit':'celsius'}
        if chosen is None:
            params['current'] = 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code'
        else:
            params.update(start_date=chosen.isoformat(),end_date=chosen.isoformat(),
                          daily='temperature_2m_min,temperature_2m_max,weather_code,precipitation_probability_max')
        response = requests.get('https://api.open-meteo.com/v1/forecast',params=params,timeout=(4,10))
        response.raise_for_status()
        payload = response.json()
        name = ', '.join(dict.fromkeys(str(place[k]) for k in ('name','admin1','country') if place.get(k)))
        if chosen is None:
            current = payload.get('current') or {}
            temp = current.get('temperature_2m')
            if temp is None: return 'Hata: Anlık sıcaklık verisi alınamadı.'
            details = f"{temp:g} derece, {_condition(current.get('weather_code'))}"
            if current.get('apparent_temperature') is not None:
                details += f", hissedilen {current['apparent_temperature']:g} derece"
            result = f"{name} için anlık hava durumu: {details}."
        else:
            daily = payload.get('daily') or {}
            dates = daily.get('time') or []
            if chosen.isoformat() not in dates: return 'Hata: İstenen güne ait tahmin alınamadı; başka günün verisini kullanma.'
            i = dates.index(chosen.isoformat())
            low, high = daily['temperature_2m_min'][i], daily['temperature_2m_max'][i]
            if low is None or high is None: return 'Hata: İstenen günün sıcaklık tahmini eksik.'
            result = f"{name}, {chosen.strftime('%d.%m.%Y')} tahmini: en düşük {low:g}, en yüksek {high:g} derece; {_condition(daily['weather_code'][i])}."
            rain = (daily.get('precipitation_probability_max') or [None]*len(dates))[i]
            if rain is not None: result += f' Yağış ihtimali yüzde {rain:g}.'
        if not location:
            result += f' Konum belirtilmediği için varsayılan {target} kullanıldı.'
        return result
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return 'Hata: Hava durumu servisine ulaşılamadı veya veri eksik. Sıcaklık uydurma; daha sonra tekrar dene.'
