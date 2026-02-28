import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import io
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time

# --- MAİL AYARLARI ---
GONDERICI_MAIL = "ornek_mail@gmail.com" 
GONDERICI_SIFRE = "mail_sifren_veya_app_password"
SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 587

# --- 1. VERİTABANI GÜNCELLEME VE KONTROL ---
def veritabanini_hazirla():
    conn = sqlite3.connect('tse_v4.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS denetimler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, basvuru_no TEXT, firma_adi TEXT NOT NULL, marka TEXT,
        arac_kategori TEXT, arac_tipi TEXT NOT NULL, varyant TEXT, versiyon TEXT, ticari_ad TEXT,
        gtip_no TEXT, birim TEXT, uretim_ulkesi TEXT, arac_sayisi TEXT, sasi_no TEXT UNIQUE, 
        basvuru_tarihi DATE, secim_tarihi DATE, il TEXT, durum TEXT DEFAULT 'Şasi Bekliyor',
        notlar TEXT, guncelleme_tarihi TEXT, ekleyen_kullanici TEXT, silme_talebi INTEGER DEFAULT 0, silme_nedeni TEXT)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kullanici_adi TEXT UNIQUE NOT NULL, sifre TEXT NOT NULL,
        rol TEXT NOT NULL, email TEXT, sorumlu_il TEXT, onay_durumu INTEGER DEFAULT 1, excel_yukleme_yetkisi INTEGER DEFAULT 0)''')
    
    if cursor.execute("SELECT COUNT(*) FROM kullanicilar").fetchone()[0] == 0:
        cursor.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES ('admin', 'admin123', 'admin', 'admin@tse.org.tr', 'Tümü', 1, 1)")
        cursor.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES ('ankara_uzman', '1234', 'kullanici', 'ankara@tse.org.tr', 'Ankara', 1, 0)")
        cursor.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES ('kocaeli_uzman', '1234', 'kullanici', 'kocaeli@tse.org.tr', 'Kocaeli', 1, 0)")
    conn.commit(); conn.close()

veritabanini_hazirla()

def il_cikar(birim_adi):
    if not isinstance(birim_adi, str): return "Diğer"
    b = birim_adi.upper()
    if 'İSTANBUL' in b or 'ISTANBUL' in b: return 'İstanbul'
    if 'ANKARA' in b: return 'Ankara'
    if 'İZMİR' in b or 'IZMIR' in b: return 'İzmir'
    if 'BURSA' in b: return 'Bursa'
    if 'KOCAELİ' in b or 'KOCAELI' in b: return 'Kocaeli'
    return 'Diğer'

# --- 2. OTURUM İŞLEMLERİ VE YENİ UYARI STATÜLERİ ---
if 'giris_yapildi' not in st.session_state:
    for key in ['giris_yapildi', 'kullanici_adi', 'rol', 'sorumlu_il', 'excel_yetkisi']:
        st.session_state[key] = False if key == 'giris_yapildi' else ""

for k in ['onay_bekleyen_excel', 'onay_bekleyen_manuel_ortak', 'onay_bekleyen_manuel_admin', 'onay_bekleyen_sasi_id', 'onay_bekleyen_sasi_no']:
    if k not in st.session_state: st.session_state[k] = None

def giris_yap(k_adi, sifre):
    conn = sqlite3.connect('tse_v4.db')
    kullanici = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (k_adi, sifre)).fetchone()
    conn.close()
    if kullanici:
        if kullanici[2] == 0: st.warning("⏳ Hesabınız henüz onaylanmamış.")
        else:
            st.session_state.update({'giris_yapildi': True, 'kullanici_adi': k_adi, 'rol': kullanici[0], 'sorumlu_il': kullanici[1], 'excel_yetkisi': kullanici[3]}); st.rerun()
    else: st.error("❌ Hatalı kullanıcı veya şifre!")

def cikis_yap():
    for key in ['giris_yapildi', 'kullanici_adi', 'rol', 'sorumlu_il', 'excel_yetkisi', 'onay_bekleyen_excel', 'onay_bekleyen_manuel_ortak', 'onay_bekleyen_manuel_admin', 'onay_bekleyen_sasi_id', 'onay_bekleyen_sasi_no']: 
        st.session_state[key] = False if key == 'giris_yapildi' else None
    st.rerun()

def yeni_kullanici_kaydet(k_adi, sifre, email, il):
    try:
        conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (?, ?, 'kullanici', ?, ?, 0, 0)", (k_adi, sifre, email, il)); conn.commit(); conn.close()
        return True, "✅ Kayıt talebiniz başarıyla alındı!"
    except sqlite3.IntegrityError: return False, "❌ Bu kullanıcı adı zaten kayıtlı."

def admin_kullanici_islem(k_id, islem_tipi):
    conn = sqlite3.connect('tse_v4.db')
    islemler = {'onayla': "UPDATE kullanicilar SET onay_durumu = 1 WHERE id = ?", 'sil': "DELETE FROM kullanicilar WHERE id = ?", 'yetki_ver': "UPDATE kullanicilar SET excel_yukleme_yetkisi = 1 WHERE id = ?", 'yetki_al': "UPDATE kullanicilar SET excel_yukleme_yetkisi = 0 WHERE id = ?"}
    conn.cursor().execute(islemler[islem_tipi], (k_id,)); conn.commit(); conn.close()

# --- 3. EXCEL VE MAİL ---
def excel_yukle_ve_veritabanina_yaz(df_excel, ekleyen_kisi):
    conn = sqlite3.connect('tse_v4.db'); cursor = conn.cursor(); bugun = datetime.now().strftime("%Y-%m-%d")
    for _, row in df_excel.iterrows():
        b_no, firma, marka, tip = str(row.get('BasvuruNo', '')), str(row.get('Firma', '')), str(row.get('Marka', '')), str(row.get('Tip', ''))
        varyant, versiyon = str(row.get('Varyant', '')), str(row.get('Versiyon', ''))
        
        if cursor.execute("SELECT id FROM denetimler WHERE basvuru_no=? AND arac_tipi=? AND varyant=? AND versiyon=?", (b_no, tip, varyant, versiyon)).fetchone() is None:
            cursor.execute('''INSERT INTO denetimler (basvuru_no, firma_adi, marka, arac_kategori, arac_tipi, varyant, versiyon, ticari_ad, gtip_no, birim, uretim_ulkesi, arac_sayisi, basvuru_tarihi, il, durum, ekleyen_kullanici, sasi_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Şasi Bekliyor', ?, NULL)''', (b_no, firma, marka, str(row.get('Araç Kategori', '')), tip, varyant, versiyon, str(row.get('TicariAd', '')), str(row.get('GtipNo', '')), str(row.get('Birim', '')), str(row.get('Üretildiği Ülke', '')), str(row.get('Araç Sayısı', '')), bugun, il_cikar(str(row.get('Birim', ''))), ekleyen_kisi))
    conn.commit(); conn.close()

def arka_planda_exceli_dagit_ve_mail_at(df_excel):
    time.sleep(180); conn = sqlite3.connect('tse_v4.db')
    df_excel['Mail_Ili'] = df_excel['Birim'].apply(il_cikar) if 'Birim' in df_excel.columns else 'Diğer'
    for il in df_excel['Mail_Ili'].unique():
        df_il = df_excel[df_excel['Mail_Ili'] == il].drop(columns=['Mail_Ili'])
        kullanicilar = pd.read_sql_query(f"SELECT email FROM kullanicilar WHERE sorumlu_il = '{il}'", conn)
        if not kullanicilar.empty:
            html_tablo = df_il.to_html(index=False, border=1, justify='center')
            for _, row in kullanicilar.iterrows():
                if row['email']:
                    mesaj = MIMEMultipart(); mesaj['From'], mesaj['To'], mesaj['Subject'] = GONDERICI_MAIL, row['email'], f"TSE - {il} İli Numune Seçim Listesi"
                    mesaj.attach(MIMEText(f"<html><body><h2>Merhaba, {il} ili için yeni numune araç listesi yüklenmiştir.</h2><br>{html_tablo}</body></html>", 'html'))
                    try: sunucu = smtplib.SMTP(SMTP_SUNUCU, SMTP_PORT); sunucu.starttls(); sunucu.login(GONDERICI_MAIL, GONDERICI_SIFRE); sunucu.send_message(mesaj); sunucu.quit()
                    except: pass
    conn.close()

def excel_yukleme_paneli_olustur():
    if st.session_state.onay_bekleyen_excel is not None:
        st.warning("⚠️ **UYARI:** Yüklediğiniz listedeki bazı araçların **Marka ve Tipi** sistemde daha önce farklı bir başvuru altında girilmiş! Yine de tüm listeyi tabloya eklemeye devam etmek istiyor musunuz?")
        c1, c2 = st.columns(2)
        if c1.button("✅ Devam Et (Tabloya Ekle)", use_container_width=True):
            df_yuklenen = st.session_state.onay_bekleyen_excel
            excel_yukle_ve_veritabanina_yaz(df_yuklenen, st.session_state.kullanici_adi)
            st.session_state.onay_bekleyen_excel = None
            st.success("✅ Veriler tabloya eklendi!")
            threading.Thread(target=arka_planda_exceli_dagit_ve_mail_at, args=(df_yuklenen,)).start()
            time.sleep(1.5); st.rerun()
        if c2.button("❌ Vazgeç (Hiçbir Şey Yapma)", use_container_width=True):
            st.session_state.onay_bekleyen_excel = None
            st.info("İşlem iptal edildi. Hiçbir veri eklenmedi."); time.sleep(1.5); st.rerun()
    else:
        st.info("İçerisinde yükleme şablonuna uygun sütunlar bulunan bir Excel veya CSV dosyası yükleyin.")
        yuklenen_dosya = st.file_uploader("Numune Başvuru Listesi", type=["xlsx", "csv"], key="excel_up")
        if yuklenen_dosya:
            df_yuklenen = pd.read_csv(yuklenen_dosya) if yuklenen_dosya.name.endswith('.csv') else pd.read_excel(yuklenen_dosya)
            st.dataframe(df_yuklenen.head(3))
            
            if st.button("Listeyi Sisteme Yükle ve Dağıt", use_container_width=True):
                conn = sqlite3.connect('tse_v4.db'); cursor = conn.cursor()
                cakisma_var_mi = False
                for _, row in df_yuklenen.iterrows():
                    b_no, marka, tip = str(row.get('BasvuruNo', '')), str(row.get('Marka', '')), str(row.get('Tip', ''))
                    if cursor.execute("SELECT id FROM denetimler WHERE marka=? AND arac_tipi=? AND basvuru_no != ?", (marka, tip, b_no)).fetchone():
                        cakisma_var_mi = True; break
                conn.close()
                
                if cakisma_var_mi: st.session_state.onay_bekleyen_excel = df_yuklenen; st.rerun()
                else:
                    excel_yukle_ve_veritabanina_yaz(df_yuklenen, st.session_state.kullanici_adi)
                    st.success("✅ Veriler tabloya eklendi!")
                    threading.Thread(target=arka_planda_exceli_dagit_ve_mail_at, args=(df_yuklenen,)).start()
                    st.info("✅ Mailler arka planda gönderilecektir.")

# --- 4. VERİ ÇEKME VE İŞLEM FONKSİYONLARI ---
def verileri_getir():
    conn = sqlite3.connect('tse_v4.db')
    df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", conn)
    conn.close()
    df['secim_tarihi'] = pd.to_datetime(df['secim_tarihi']); bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    df['Geçen Gün'] = (bugun - df['secim_tarihi']).dt.days; df['Geçen Gün'] = df['Geçen Gün'].fillna('-')
    df['secim_tarihi'] = df['secim_tarihi'].dt.strftime('%Y-%m-%d').fillna('-')
    for c in df.columns: df[c] = df[c].fillna('-')
    df['silme_talebi'] = df['silme_talebi'].apply(lambda x: "Evet" if x == 1 else "Hayır")
    istenen_sira = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'arac_kategori', 'birim', 'il']
    kalan_sutunlar = [col for col in df.columns if col not in istenen_sira]
    return df[[col for col in (istenen_sira + kalan_sutunlar) if col in df.columns]]

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.3)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.3)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.3)'] * len(row)
    return [''] * len(row)

def arac_basvurusu_yap(firma_adi, marka, arac_tipi, sasi_no, il, basvuru_no, basvuru_tarihi, kullanici):
    conn = sqlite3.connect('tse_v4.db'); cursor = conn.cursor()
    # Artık direkt Teste Gönderildi yapıyoruz (Manuel tekli kayıt için)
    durum = 'Teste Gönderildi'
    s_tarihi = datetime.now().strftime("%Y-%m-%d")
    msj = f"✅ {sasi_no} başarıyla kaydedildi ve TESTE GÖNDERİLDİ!"
    tip = "success"
    
    if hasattr(basvuru_tarihi, 'strftime'): b_tarihi_str = basvuru_tarihi.strftime("%Y-%m-%d")
    else: b_tarihi_str = str(basvuru_tarihi)
        
    try:
        cursor.execute('''INSERT INTO denetimler (firma_adi, arac_tipi, sasi_no, basvuru_tarihi, secim_tarihi, il, durum, basvuru_no, ekleyen_kullanici, marka, arac_kategori, varyant, versiyon, ticari_ad, gtip_no, birim, uretim_ulkesi, arac_sayisi) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '-', '-', '-', '-', '-', '-', '-', '-')''', (firma_adi, arac_tipi, sasi_no, b_tarihi_str, s_tarihi, il, durum, basvuru_no, kullanici, marka))
        conn.commit()
    except sqlite3.IntegrityError: msj, tip = f"❌ {sasi_no} zaten sistemde!", "error"
    conn.close(); return msj, tip

def manuel_kayit_formu_olustur(tab_id):
    if st.session_state[f'onay_bekleyen_manuel_{tab_id}'] is not None:
        st.warning("⚠️ **UYARI:** Girdiğiniz **Marka ve Tip** kombinasyonu daha önce farklı bir başvuru numarasıyla kaydedilmiş! Yine de tabloya eklemek istiyor musunuz?")
        c1, c2 = st.columns(2)
        if c1.button("✅ Devam Et (Tabloya Ekle)", key=f"devam_{tab_id}", use_container_width=True):
            data = st.session_state[f'onay_bekleyen_manuel_{tab_id}']
            msj, m_tip = arac_basvurusu_yap(**data)
            st.session_state[f'onay_bekleyen_manuel_{tab_id}'] = None
            st.success(msj) if m_tip == "success" else st.info(msj) if m_tip == "info" else st.error(msj)
            time.sleep(1.5); st.rerun()
        if c2.button("❌ Vazgeç (Hiçbir Şey Yapma)", key=f"vazgec_{tab_id}", use_container_width=True):
            st.session_state[f'onay_bekleyen_manuel_{tab_id}'] = None
            st.info("İşlem iptal edildi."); time.sleep(1.5); st.rerun()
    else:
        with st.form(f"arac_formu_manuel_{tab_id}", clear_on_submit=True):
            col_f1, col_f2 = st.columns(2)
            with col_f1:
                b_no = st.text_input("Gümrük Başvuru Numarası")
                firma = st.text_input("Firma Adı")
                marka = st.text_input("Araç Markası")
                tip = st.text_input("Araç Tipi / Modeli")
            with col_f2:
                b_tarihi = st.date_input("Gümrük Başvuru Tarihi")
                sasi = st.text_input("Şasi Numarası (VIN)")
                v_il = ["İstanbul", "Ankara", "İzmir", "Bursa", "Kocaeli", "Diğer"].index(st.session_state.sorumlu_il) if st.session_state.sorumlu_il in ["İstanbul", "Ankara", "İzmir", "Bursa", "Kocaeli", "Diğer"] else 0
                il = st.selectbox("Başvuru İli", ["İstanbul", "Ankara", "İzmir", "Bursa", "Kocaeli", "Diğer"], index=v_il)
            
            if st.form_submit_button("Manuel Kaydet", use_container_width=True):
                if firma and marka and tip and sasi and b_no:
                    conn = sqlite3.connect('tse_v4.db')
                    eski_basvuru = conn.cursor().execute("SELECT basvuru_no FROM denetimler WHERE marka=? AND arac_tipi=? AND basvuru_no != ?", (marka, tip, b_no)).fetchone()
                    conn.close()
                    if eski_basvuru:
                        st.session_state[f'onay_bekleyen_manuel_{tab_id}'] = {'firma_adi': firma, 'marka': marka, 'arac_tipi': tip, 'sasi_no': sasi, 'il': il, 'basvuru_no': b_no, 'basvuru_tarihi': b_tarihi, 'kullanici': st.session_state.kullanici_adi}
                        st.rerun()
                    else:
                        msj, m_tip = arac_basvurusu_yap(firma, marka, tip, sasi, il, b_no, b_tarihi, st.session_state.kullanici_adi)
                        st.success(msj) if m_tip == "success" else st.info(msj) if m_tip == "info" else st.error(msj)
                else: st.warning("Zorunlu alanları doldurun!")

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db'); guncelleme_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); sil_durum = 1 if talep_et_silme else 0
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, guncelleme_ani, sil_durum, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, guncelleme_ani, sil_durum, silme_nedeni, kayit_id))
    conn.commit(); conn.close()

def ayni_basvuruya_yeni_sasi_ekle(eski_id, yeni_sasi, ekleyen_kullanici):
    conn = sqlite3.connect('tse_v4.db'); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    eski_row = cursor.execute("SELECT * FROM denetimler WHERE id=?", (eski_id,)).fetchone()
    if not eski_row: conn.close(); return False, "Kayıt bulunamadı!"
    
    durum = 'Teste Gönderildi'
    s_tarihi = datetime.now().strftime("%Y-%m-%d")
    msj = f"✅ {yeni_sasi} eklendi ve TESTE GÖNDERİLDİ!"
    
    try:
        cursor.execute('''INSERT INTO denetimler (basvuru_no, firma_adi, marka, arac_kategori, arac_tipi, varyant, versiyon, ticari_ad, gtip_no, birim, uretim_ulkesi, arac_sayisi, sasi_no, basvuru_tarihi, secim_tarihi, il, durum, ekleyen_kullanici, notlar) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (eski_row['basvuru_no'], eski_row['firma_adi'], eski_row['marka'], eski_row['arac_kategori'], eski_row['arac_tipi'], eski_row['varyant'], eski_row['versiyon'], eski_row['ticari_ad'], eski_row['gtip_no'], eski_row['birim'], eski_row['uretim_ulkesi'], eski_row['arac_sayisi'], yeni_sasi, eski_row['basvuru_tarihi'], s_tarihi, eski_row['il'], durum, ekleyen_kullanici, "Aynı başvuruya ilave araç"))
        conn.commit(); b = True
    except sqlite3.IntegrityError: b, msj = False, f"❌ HATA: {yeni_sasi} şasi numarası sistemde mevcut!"
    conn.close(); return b, msj

# --- 5. ARAYÜZ ---
st.set_page_config(page_title="TSE Denetim Portalı", layout="wide", page_icon="🚗")

if not st.session_state.giris_yapildi:
    col_g1, col_g2, col_g3 = st.columns([1, 2, 1])
    with col_g2:
        st.title("🚗 TSE Denetim Portalı")
        tab_giris, tab_kayit = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        with tab_giris:
            with st.form("login_form"):
                k_adi, sifre = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Sisteme Giriş Yap", use_container_width=True): 
                    giris_yap(k_adi, sifre) if k_adi and sifre else st.warning("Bilgileri giriniz.")
        with tab_kayit:
            with st.form("register_form", clear_on_submit=True):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password"), st.text_input("E-Posta"), st.selectbox("Görev İli", ["İstanbul", "Ankara", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Talep Gönder", use_container_width=True):
                    if yk and ys and ye:
                        b, m = yeni_kullanici_kaydet(yk, ys, ye, yil)
                        st.success(m) if b else st.error(m)
                    else: st.warning("Eksik bilgi!")
    st.stop()

df = verileri_getir()

with st.sidebar:
    st.header("👤 Kullanıcı Profili")
    st.write(f"**Ad:** {st.session_state.kullanici_adi}")
    st.write(f"**Rol:** {st.session_state.rol.capitalize()}")
    st.write(f"**İl:** {st.session_state.sorumlu_il}")
    if st.session_state.excel_yetkisi == 1: st.write("✅ *Excel Yetkisi Var*")
    st.divider()
    if st.button("🚪 Çıkış Yap", use_container_width=True): cikis_yap()

st.title("TSE Araç İthalat Denetim Merkezi")

if st.session_state.rol == "admin": tab1, tab2, tab3, tab4 = st.tabs(["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi", "👑 Yönetici Paneli"])
else: tab1, tab2, tab3 = st.tabs(["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi"])

with tab1:
    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    col_m1.metric("Toplam Kayıt", len(df))
    col_m2.metric("Şasi Bekleyen", len(df[df['durum'] == 'Şasi Bekliyor']))
    col_m3.metric("Teste Gönderildi", len(df[df['durum'] == 'Teste Gönderildi']))
    col_m4.metric("Tamamlandı (Olumlu)", len(df[df['durum'] == 'Tamamlandı - Olumlu']))
    col_m5.metric("Tamamlandı (Olumsuz)", len(df[df['durum'] == 'Tamamlandı - Olumsuz']))
    
    st.dataframe(df.style.apply(satir_boya, axis=1), use_container_width=True, height=600)
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Rapor')
    st.download_button("📥 Güncel Tabloyu Excel Olarak İndir", buffer.getvalue(), f"TSE_Rapor_{datetime.now().strftime('%Y-%m-%d')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab2:
    st.subheader("Numune Kayıt Girişi")
    islem_df = df if st.session_state.rol == "admin" else df[(df['il'] == st.session_state.sorumlu_il) | (df['ekleyen_kullanici'] == st.session_state.kullanici_adi)]
    
    if not islem_df.empty:
        bekleyen_df = islem_df[(islem_df['durum'] == 'Şasi Bekliyor') | (islem_df['sasi_no'] == '-')]
        islem_gormus_df = islem_df[(islem_df['durum'] != 'Şasi Bekliyor') & (islem_df['sasi_no'] != '-')]
        
        col_t2_1, col_t2_2 = st.columns(2)
        
        with col_t2_1:
            st.markdown("#### 🆕 İlk Şasi Bekleyen Başvurular")
            
            pending_id = st.session_state.get('onay_bekleyen_sasi_id')
            if pending_id is not None:
                mevcut_pend = df[df['id'] == pending_id].iloc[0]
                st.warning(f"⚠️ **2. KONTROL UYARISI:** **{mevcut_pend['firma_adi']}** firmasının **{mevcut_pend['marka']} - {mevcut_pend['arac_tipi']}** aracı için sistemde daha önce numune ayrılmıştır! Liste yüklenirken bu durum gözden kaçmış olabilir.\n\nYine de bu şasiyi kaydedip **TESTE GÖNDERMEK** istiyor musunuz?")
                
                c_onay1, c_onay2 = st.columns(2)
                if c_onay1.button("✅ Eminim, Kaydet ve Teste Gönder", use_container_width=True):
                    sasi_to_save = st.session_state.get('onay_bekleyen_sasi_no')
                    bugun = datetime.now().strftime("%Y-%m-%d")
                    durum_guncelle_by_id(pending_id, sasi_to_save, 'Teste Gönderildi', mevcut_pend['notlar'], starih=bugun)
                    st.session_state['onay_bekleyen_sasi_id'] = None
                    st.session_state['onay_bekleyen_sasi_no'] = None
                    st.success("✅ Şasi eklendi: TESTE GÖNDERİLDİ")
                    time.sleep(1.5); st.rerun()
                    
                if c_onay2.button("❌ Vazgeç (İptal)", use_container_width=True):
                    st.session_state['onay_bekleyen_sasi_id'] = None
                    st.session_state['onay_bekleyen_sasi_no'] = None
                    st.info("İşlem iptal edildi."); time.sleep(1.5); st.rerun()
            else:
                if not bekleyen_df.empty:
                    bekleyen_liste = bekleyen_df['id'].astype(str) + " | Başvuru: " + bekleyen_df['basvuru_no'] + " (" + bekleyen_df['firma_adi'] + ")"
                    secilen_bekleyen = st.selectbox("İlk şasisi girilecek başvuruyu listeden seçin:", options=bekleyen_liste.tolist(), index=None, placeholder="Açılır listeden seçin...")
                    
                    if secilen_bekleyen:
                        s_id = int(secilen_bekleyen.split(" |")[0])
                        mevcut = bekleyen_df[bekleyen_df['id'] == s_id].iloc[0]
                        st.warning(f"⚠️ **Başvuru No:** {mevcut['basvuru_no']} için henüz şasi atanmamış.")
                        
                        with st.form("sasi_giris_form"):
                            yeni_sasi = st.text_input("Şasi Numarası (VIN) Giriniz")
                            if st.form_submit_button("Kaydet ve Değerlendir", use_container_width=True):
                                if yeni_sasi:
                                    conn = sqlite3.connect('tse_v4.db')
                                    onceki = conn.cursor().execute('SELECT id FROM denetimler WHERE firma_adi=? AND marka=? AND arac_tipi=? AND secim_tarihi IS NOT NULL AND id != ?', (mevcut['firma_adi'], mevcut['marka'], mevcut['arac_tipi'], s_id)).fetchone()
                                    conn.close()
                                    bugun = datetime.now().strftime("%Y-%m-%d")
                                    
                                    if onceki is not None:
                                        st.session_state['onay_bekleyen_sasi_id'] = s_id
                                        st.session_state['onay_bekleyen_sasi_no'] = yeni_sasi
                                        st.rerun()
                                    else:
                                        durum_guncelle_by_id(s_id, yeni_sasi, 'Teste Gönderildi', mevcut['notlar'], starih=bugun)
                                        st.success("✅ Şasi eklendi: TESTE GÖNDERİLDİ")
                                        time.sleep(1.5); st.rerun()
                                else: st.error("Şasi boş olamaz!")
                else:
                    st.success("Tebrikler! İlk şasi atanması bekleyen başvuru bulunmuyor.")
                
        with col_t2_2:
            st.markdown("#### 🔍 İlave Şasi Ekleme ve Güncelleme")
            if not islem_gormus_df.empty:
                islem_gormus_liste = islem_gormus_df['id'].astype(str) + " | Başvuru: " + islem_gormus_df['basvuru_no'] + " | Şasi: " + islem_gormus_df['sasi_no'] + " (" + islem_gormus_df['firma_adi'] + ")"
                secilen_islem_gormus = st.selectbox("Aramak için Başvuru No, Firma veya Şasi yazmaya başlayın:", options=islem_gormus_liste.tolist(), index=None, placeholder="🔍 Örn: SASI... (Yazın veya seçin)")
                
                if secilen_islem_gormus:
                    s_id = int(secilen_islem_gormus.split(" |")[0])
                    mevcut = islem_gormus_df[islem_gormus_df['id'] == s_id].iloc[0]
                    st.info(f"**Bulunan Araç:** {mevcut['sasi_no']} | **Durum:** {mevcut['durum']}")
                    
                    tab_upd, tab_add = st.tabs(["Durum Güncelle", "➕ İlave Araç (Klonla)"])
                    with tab_upd:
                        with st.form("k_guncelleme"):
                            t_durumlar = ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz", "Reddedildi", "Şasi Bekliyor"]
                            v_idx = t_durumlar.index(mevcut['durum']) if mevcut['durum'] in t_durumlar else 0
                            y_durum = st.selectbox("Durum", t_durumlar, index=v_idx)
                            notlar = st.text_area("Ek Notlar", value=mevcut['notlar'] if mevcut['notlar'] != '-' else "")
                            sil_istek = st.checkbox("Tamamen SİLME talebi oluştur" if st.session_state.rol != 'admin' else "Aracı Tamamen SİL")
                            sil_neden = st.text_input("Nedeni:") if sil_istek else ""
                            
                            if st.form_submit_button("Değişiklikleri Kaydet", use_container_width=True):
                                if sil_istek:
                                    if st.session_state.rol == 'admin':
                                        conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute('DELETE FROM denetimler WHERE id=?', (s_id,)); conn.commit(); conn.close(); st.success("Silindi!"); time.sleep(1); st.rerun()
                                    elif not sil_neden: st.error("Silme nedeni belirtin!")
                                    else: durum_guncelle_by_id(s_id, mevcut['sasi_no'], y_durum, notlar, talep_et_silme=True, silme_nedeni=sil_neden); st.success("Talep gönderildi!"); time.sleep(1); st.rerun()
                                else:
                                    durum_guncelle_by_id(s_id, mevcut['sasi_no'], y_durum, notlar); st.success("Kaydedildi!"); time.sleep(1); st.rerun()
                    
                    with tab_add:
                        with st.form("ilave_sasi_form"):
                            st.write("Bu başvurunun (Excel) verileri kopyalanarak yeni araç eklenecektir.")
                            yeni_ekstra_sasi = st.text_input("Yeni Şasi Numarası (VIN)")
                            if st.form_submit_button("İlave Aracı Ekle", use_container_width=True):
                                if yeni_ekstra_sasi:
                                    basari, msj = ayni_basvuruya_yeni_sasi_ekle(s_id, yeni_ekstra_sasi, st.session_state.kullanici_adi)
                                    if basari: st.success(msj); time.sleep(1.5); st.rerun()
                                    else: st.error(msj)
                                else: st.error("Şasi numarası girin!")
            else:
                st.info("İşlem görmüş (şasisi girilmiş) kayıt bulunmuyor.")
    else: st.info("Size atanmış bir kayıt bulunamadı.")

with tab3:
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        st.subheader("✍️ Elden Tekli Başvuru")
        manuel_kayit_formu_olustur('ortak')
    
    with col_v2:
        st.subheader("📥 Excel İle Toplu Yükleme")
        if st.session_state.rol == "admin" or st.session_state.excel_yetkisi == 1: excel_yukleme_paneli_olustur()
        else: st.warning("Toplu liste yükleme yetkiniz bulunmamaktadır.")

if st.session_state.rol == "admin":
    with tab4:
        conn = sqlite3.connect('tse_v4.db'); df_k = pd.read_sql_query("SELECT id, kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar", conn); conn.close()
        
        st.subheader("👥 Kullanıcı Yönetimi")
        col_a1, col_a2 = st.columns(2)
        with col_a1:
            with st.expander("⏳ Onay Bekleyen Kullanıcılar", expanded=True):
                for _, row in df_k[df_k['onay_durumu'] == 0].iterrows():
                    c1, c2, c3 = st.columns([2, 1, 1])
                    c1.write(f"**{row['kullanici_adi']}** ({row['sorumlu_il']})")
                    if c2.button("Onayla", key=f"o_{row['id']}", type="primary"): admin_kullanici_islem(row['id'], 'onayla'); st.rerun()
                    if c3.button("Sil", key=f"s_{row['id']}"): admin_kullanici_islem(row['id'], 'sil'); st.rerun()
                if len(df_k[df_k['onay_durumu'] == 0]) == 0: st.info("Bekleyen talep yok.")
        with col_a2:
            with st.expander("🌟 Excel Yükleme Yetkisi Ver/Al", expanded=True):
                secili = st.selectbox("Kullanıcı Seç", options=df_k[df_k['onay_durumu'] == 1]['id'], format_func=lambda x: df_k[df_k['id']==x]['kullanici_adi'].values[0])
                if secili:
                    if df_k[df_k['id']==secili]['excel_yukleme_yetkisi'].values[0] == 0:
                        if st.button("Yetki Ver", use_container_width=True): admin_kullanici_islem(secili, 'yetki_ver'); st.rerun()
                    else:
                        if st.button("Yetkiyi Kaldır", use_container_width=True): admin_kullanici_islem(secili, 'yetki_al'); st.rerun()

        with st.expander("✅ Aktif Kullanıcılar Listesi"):
            onaylanmislar = df_k[df_k['onay_durumu'] == 1].copy()
            onaylanmislar['Excel Yetkisi'] = onaylanmislar['excel_yukleme_yetkisi'].apply(lambda x: "🟢 Var" if x == 1 else "🔴 Yok")
            st.dataframe(onaylanmislar[['id', 'kullanici_adi', 'sifre', 'rol', 'sorumlu_il', 'email', 'Excel Yetkisi']], hide_index=True)

        st.write("---")
        st.subheader("🗑️ Araç Silme Talepleri")
        talepler = df[df['silme_talebi'] == "Evet"]
        if not talepler.empty:
            for _, row in talepler.iterrows():
                with st.container(border=True):
                    st.write(f"**Şasi:** {row['sasi_no']} | **Ekleyen:** {row['ekleyen_kullanici']} | **Neden:** {row.get('silme_nedeni', 'Belirtilmemiş')}")
                    c_s1, c_s2, c_s3 = st.columns([1, 1, 3])
                    if c_s1.button("Onayla & Sil", key=f"sil_{row['id']}", type="primary"):
                        conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute('DELETE FROM denetimler WHERE id=?', (row['id'],)); conn.commit(); conn.close(); st.rerun()
                    if c_s2.button("Reddet", key=f"red_{row['id']}"):
                        conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute('UPDATE denetimler SET silme_talebi=0, silme_nedeni=NULL WHERE id=?', (row['id'],)); conn.commit(); conn.close(); st.rerun()
        else: st.success("Bekleyen araç silme talebi yok.")