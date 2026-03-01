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
import os # YENİ: Dosya (logo) kontrolü için eklendi

# --- KULLANIM KILAVUZU METNİ ---
KILAVUZ_METNI = """# 🇹🇷 TSE NUMUNE TAKİP PORTALI - KULLANIM KILAVUZU VE SİSTEM ÖZETİ

Bu proje, kurum içindeki başvuru, numune atama (şasi eşleştirme) ve denetim süreçlerini dijitalleştirmek, kullanıcıları illere göre yönetmek ve süreçleri otomatik e-posta bildirimleriyle hızlandırmak amacıyla geliştirilmiştir.

## 🛠 1. Teknik Altyapı ve Güvenlik
* Arayüz (UI): Kullanıcı dostu Streamlit altyapısı kullanılmıştır.
* Veritabanı: Hızlı ve güvenilir SQLite kullanılmıştır. Çoklu kullanıcı erişimi için optimize edilmiştir.
* Veri Güvenliği: Şifreler ve e-posta sunucu bilgileri güvenli "Secrets" kasasında saklanmaktadır.

## 👥 2. Rol ve Oturum Yönetimi
Sistemde iki farklı kullanıcı rolü bulunmaktadır: Kullanıcı ve Admin (Yönetici).
* Yeni kayıt olan bir kullanıcı sisteme yöneticinin onayından sonra girebilir.
* Yöneticiler tüm illerin verilerini görebilirken, standart kullanıcılar sadece kendi sorumlu oldukları illerin verilerini yönetebilirler.

## 🖥 3. Sistem Sekmeleri ve Fonksiyonlar

### 📊 Sekme 1: Ana Tablo (Sistem Kayıtları)
Tüm verilerin izlendiği ana gösterge panelidir.
* Özet Metrikler ve Renkli Durum Göstergeleri sunar.
* Akıllı Arama ile tüm tabloda filtreleme yapılabilir.
* Tablodaki veriler tek tıkla Excel (.xlsx) formatında bilgisayara indirilebilir.

### 🛠️ Sekme 2: İşlem Paneli (Numune Kayıt Girişi)
* Şasi Atama: "Şasi Bekliyor" durumundaki başvurulara VIN numarası girilerek "Teste Gönderildi" aşamasına geçirilir. Çift kayıt uyarısı ile koruma altındadır.
* Güncelleme & İlave: Araçların durumları güncellenir veya silme talebi oluşturulabilir.

### 📥 Sekme 3: Veri Girişi (Manuel & Excel)
* Elden Kayıt: Tekil kayıtlar form aracılığıyla eklenebilir.
* Excel ile Toplu Yükleme: Sütun eşleştirme, akıllı il tahmini ve mükerrer firma/marka/tip kontrolü yapılarak veriler güvenle sisteme aktarılır.

### 👑 Sekme 4: Yönetici Paneli (Sadece Adminler)
* Onay bekleyen üyeler ve silme talepleri yönetilir.
* Kullanıcı Yönetimi: Excel yükleme yetkisi verilebilir, doğrudan kayıt veya kullanıcı hesabı kalıcı olarak silinebilir.

## 📧 4. Arka Plan Otomasyonları (Mail Bildirimleri)
* Yeni üye kaydı ve silme talebi bildirimleri yöneticiye anında iletilir.
* Excel yüklendiğinde, sistem hangi ile kaç kayıt düştüğünü hesaplar ve SADECE o ilden sorumlu onaylı kullanıcılara otomatik bilgilendirme e-postası gönderir.
"""

# --- SAYFA YAPILANDIRMASI ---
st.set_page_config(page_title="TSE NUMUNE TAKİP PORTALI", layout="wide")

# --- TSE KURUMSAL VE MAİL AYARLARI ---
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "") 
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets (Mail ayarları) bulunamadı!")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 

# --- 1. VERİTABANI MOTORU ---
def veritabanini_hazirla():
    with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
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
        conn.commit()

veritabanini_hazirla()

# --- BİLDİRİM MOTORU ---
def admin_bildirim_mail_at(konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, ADMIN_MAIL, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirim</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
    except: pass

def kullanici_bildirim_mail_at(kime_mail, konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, kime_mail, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirim</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
    except: pass

# --- YARDIMCI İŞLEMLER ---
def excel_kaydet_ve_mail_at(df_yeni, atlanan_sayi):
    """Excel verilerini veritabanına yazar ve mail bildirimlerini gönderir"""
    mail_gidenler = []
    with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
        df_yeni.to_sql('denetimler', conn, if_exists='append', index=False)
        
        try:
            il_ozeti = df_yeni['il'].value_counts().to_dict()
            cursor = conn.cursor()
            for il_adi, adet in il_ozeti.items():
                ilgili_kullanicilar = cursor.execute("SELECT email, kullanici_adi FROM kullanicilar WHERE sorumlu_il=? AND onay_durumu=1", (il_adi,)).fetchall()
                for k_mail, k_adi in ilgili_kullanicilar:
                    if k_mail and "@" in k_mail: 
                        m_konu = f"TSE Sistemi - {il_adi} İli İçin Yeni Veri Girişi"
                        m_icerik = f"Merhaba <b>{k_adi}</b>,<br><br>Sistemde sorumlu olduğunuz <b>{il_adi}</b> ili için sisteme <b>{adet} adet</b> yeni kayıt yüklenmiştir. Lütfen portal üzerinden numune/şasi atama işlemlerini tamamlayınız."
                        threading.Thread(target=kullanici_bildirim_mail_at, args=(k_mail, m_konu, m_icerik)).start()
                        mail_gidenler.append(f"{k_adi} ({il_adi})")
        except Exception as mail_hata:
            st.warning(f"Uyarı: Kayıtlar eklendi ancak mail gönderilirken bir hata oluştu: {mail_hata}")
    
    eklenen_sayi = len(df_yeni)
    mesaj = f"Tebrikler! {eklenen_sayi} adet YENİ kayıt başarıyla aktarıldı."
    if atlanan_sayi > 0:
        mesaj += f" ({atlanan_sayi} adet mevcut başvuru numarası mükerrer olduğu için atlandı.)"
    if len(mail_gidenler) > 0:
        mesaj += f" Bildirim iletilenler: {', '.join(mail_gidenler)}"
        
    st.success(mesaj)
    time.sleep(3)
    st.rerun()

# --- 2. DURUM SORGULARI ---
def durum_sayilarini_al():
    with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
        onay_sayisi = conn.execute("SELECT COUNT(*) FROM kullanicilar WHERE onay_durumu = 0").fetchone()[0]
        silme_sayisi = conn.execute("SELECT COUNT(*) FROM denetimler WHERE silme_talebi = 1").fetchone()[0]
    return onay_sayisi, silme_sayisi

def verileri_getir():
    with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
        df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", conn)
    
    df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'])
    bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days.apply(lambda x: str(int(x)) if pd.notnull(x) else '-')
    df['secim_tarihi'] = df['secim_tarihi_dt'].dt.strftime('%Y-%m-%d').fillna('-')
    for c in df.columns: 
        if c not in ['Geçen Gün', 'secim_tarihi_dt']: df[c] = df[c].fillna('-')
    return df

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.3)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.3)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.3)'] * len(row)
    return [''] * len(row)

# --- 3. OTURUM YÖNETİMİ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({
        'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': "", 'excel_yetkisi': 0,
        'onay_bekleyen_excel_df': None, 'atlanan_kayit_sayisi': 0
    })

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sil_v = 1 if talep_et_silme else 0
    with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
        if starih == "MEVCUT": 
            conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
        else: 
            conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
        conn.commit()
        
    if talep_et_silme: threading.Thread(target=admin_bildirim_mail_at, args=("⚠️ YENİ SİLME TALEBİ", f"{sasi_no} için silme talebi var.")).start()

# --- 4. GİRİŞ EKRANI ---
if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        # YENİ: LOGO EKLENTİSİ (GİRİŞ EKRANI)
        if os.path.exists("tse_logo.png"):
            # Logoyu tam ortalamak için küçük kolonlar kullanıyoruz
            logo_c1, logo_c2, logo_c3 = st.columns([1, 2, 1])
            with logo_c2:
                st.image("tse_logo.png", use_container_width=True)
                
        st.markdown("<h1 style='text-align: center; color: #E03131;'> TSE NUMUNE TAKİP PORTALI</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        with tg:
            with st.form("login_form"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
                        u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone()
                    
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1], 'excel_yetkisi':u[3]}); st.rerun()
                    else: st.error("❌ Hatalı bilgiler.")
        with tk:
            with st.form("register_form"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Talebi Gönder"):
                    try:
                        with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
                            conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (?, ?, 'kullanici', ?, ?, 0, 0)", (yk, ys, ye, yil))
                            conn.commit()
                        threading.Thread(target=admin_bildirim_mail_at, args=("📝 YENİ KAYIT", f"Yeni üye talebi: {yk}")).start()
                        st.success("Tebrikler! Talebiniz iletildi."); time.sleep(1); st.rerun()
                    except: st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- 5. ANA EKRAN (GİRİŞ SONRASI) ---
b_onay, b_silme = durum_sayilarini_al()
toplam_bekleyen = b_onay + b_silme
df = verileri_getir()

with st.sidebar:
    # YENİ: LOGO EKLENTİSİ (YAN MENÜ)
    if os.path.exists("tse_logo.png"):
        st.image("tse_logo.png", use_container_width=True)
        
    st.markdown("<h2 style='color: #E03131;'>TSE PANEL</h2>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    st.write(f"📍 **{st.session_state.sorumlu_il}**")
    if st.session_state.rol == "admin" and toplam_bekleyen > 0:
        st.error(f"🚨 {toplam_bekleyen} Bekleyen İşlem!")
    
    st.divider()
    
    st.download_button(
        label="📄 Kullanım Kılavuzunu İndir",
        data=KILAVUZ_METNI,
        file_name="TSE_Denetim_Portali_Kullanim_Kilavuzu.md",
        mime="text/markdown",
        use_container_width=True
    )
    
    st.divider()
    
    if st.button("🚪 Oturumu Kapat", use_container_width=True):
        st.session_state.clear(); st.rerun()

if st.session_state.rol == "admin" and toplam_bekleyen > 0:
    st.error(f"📢 **Yönetici Bildirimi:** Şu an onay bekleyen **{b_onay} üye** ve **{b_silme} silme talebi** var.")

admin_tab_label = f"👑 Yönetici Paneli ({toplam_bekleyen})" if (st.session_state.rol == "admin" and toplam_bekleyen > 0) else "👑 Yönetici Paneli"
main_tabs = ["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi"]
if st.session_state.rol == "admin": main_tabs.append(admin_tab_label)

tabs = st.tabs(main_tabs)

with tabs[0]:
    st.subheader("Sistem Kayıtları")
    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Toplam", len(df))
    c_m2.metric("Teste Gönderildi", len(df[df['durum'] == 'Teste Gönderildi']))
    c_m3.metric("Olumlu", len(df[df['durum'] == 'Tamamlandı - Olumlu']))
    
    istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'arac_kategori', 'birim', 'il']
    display_df = df[[c for c in istenen if c in df.columns] + [c for c in df.columns if c not in istenen and c not in ['secim_tarihi_dt', 'silme_talebi']]]
    
    src = st.text_input("🔍 Filtrele (Şasi, Marka, Firma vb.):")
    if src: display_df = display_df[display_df.apply(lambda r: src.lower() in r.astype(str).str.lower().values, axis=1)]
    
    st.dataframe(display_df.style.apply(satir_boya, axis=1), use_container_width=True, height=800)
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as w: display_df.to_excel(w, index=False)
    st.download_button("📥 Excel İndir", buffer.getvalue(), f"TSE_Rapor_{datetime.now().strftime('%Y-%m-%d')}.xlsx")

with tabs[1]:
    st.subheader("İşlem Paneli")
    i_df = df if st.session_state.rol == "admin" else df[(df['il'] == st.session_state.sorumlu_il) | (df['ekleyen_kullanici'] == st.session_state.kullanici_adi)]
    
    p_id = st.session_state.get('onay_bekleyen_sasi_id')
    
    if p_id:
        st.warning("⚠️ DİKKAT: Bu Firma, Marka ve Araç Tipi kombinasyonuna sahip başka bir kayıt zaten sistemde mevcut! Yine de bu şasiyi kaydetmek istiyor musunuz?")
        c_evet, c_hayir = st.columns(2)
        
        with c_evet:
            if st.button("✅ Devam (Kaydet)", use_container_width=True):
                try:
                    durum_guncelle_by_id(p_id, st.session_state.onay_bekleyen_sasi_no, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
                    st.session_state.update({'onay_bekleyen_sasi_id': None, 'onay_bekleyen_sasi_no': None}); st.rerun()
                except sqlite3.IntegrityError:
                    st.error("❌ Hata: Bu Şasi Numarası sistemde zaten mevcut!")
                    st.session_state.update({'onay_bekleyen_sasi_id': None, 'onay_bekleyen_sasi_no': None})
        
        with c_hayir:
            if st.button("❌ Vazgeç (İptal)", use_container_width=True):
                st.session_state.update({'onay_bekleyen_sasi_id': None, 'onay_bekleyen_sasi_no': None})
                st.rerun()
    else:
        c_left, c_right = st.columns(2)
        with c_left:
            st.markdown("#### 🆕 Şasi Atama")
            b_list = i_df[i_df['durum'] == 'Şasi Bekliyor']
            sel = st.selectbox("Başvuru:", options=(b_list['id'].astype(str) + " | " + b_list['basvuru_no'].astype(str)).tolist(), index=None)
            if sel:
                sid = int(sel.split(" |")[0]); row_m = b_list[b_list['id'] == sid].iloc[0]
                vin = st.text_input("VIN Numarası")
                if st.button("Kaydet ve Teste Gönder"):
                    if not vin.strip():
                        st.error("Lütfen bir Şasi (VIN) Numarası giriniz!")
                    else:
                        try:
                            with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
                                once = conn.cursor().execute('SELECT id FROM denetimler WHERE firma_adi=? AND marka=? AND arac_tipi=? AND id != ?', (row_m['firma_adi'], row_m['marka'], row_m['arac_tipi'], sid)).fetchone()
                            
                            if once: 
                                st.session_state.update({'onay_bekleyen_sasi_id': sid, 'onay_bekleyen_sasi_no': vin}); st.rerun()
                            else: 
                                durum_guncelle_by_id(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d")); st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("❌ Hata: Bu Şasi Numarası sistemde zaten kayıtlı!")
                            
        with c_right:
            st.markdown("#### 🔍 Güncelleme & İlave")
            i_list = i_df[i_df['durum'] != 'Şasi Bekliyor']
            srch = st.selectbox("Şasi/Firma Ara:", options=(i_list['id'].astype(str) + " | " + i_list['sasi_no'].astype(str)).tolist(), index=None)
            if srch:
                sid = int(srch.split(" |")[0]); cur = i_list[i_list['id'] == sid].iloc[0]
                with st.form("upd_form"):
                    nd = st.selectbox("Yeni Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz", "Reddedildi"])
                    sl = st.checkbox("Silme Talebi")
                    if st.form_submit_button("Güncelle"):
                        durum_guncelle_by_id(sid, cur['sasi_no'], nd, "", talep_et_silme=sl, silme_nedeni="Talep Edildi")
                        st.rerun()

with tabs[2]:
    st.subheader("📥 Veri Girişi")
    
    if st.session_state.get('onay_bekleyen_excel_df') is not None:
        st.warning("⚠️ DİKKAT: Yüklemeye çalıştığınız dosyadaki bazı kayıtların 'Firma, Marka ve Araç Tipi' bilgileri sistemde zaten mevcut! Yine de tabloya eklemek istiyor musunuz?")
        
        co1, co2 = st.columns(2)
        with co1:
            if st.button("✅ Devam (Tabloya Ekle)", use_container_width=True):
                df_gecici = st.session_state.onay_bekleyen_excel_df
                atlanmis = st.session_state.atlanan_kayit_sayisi
                
                st.session_state.onay_bekleyen_excel_df = None
                st.session_state.atlanan_kayit_sayisi = 0
                
                excel_kaydet_ve_mail_at(df_gecici, atlanmis)
                
        with co2:
            if st.button("❌ Vazgeç (İptal Et)", use_container_width=True):
                st.session_state.onay_bekleyen_excel_df = None
                st.session_state.atlanan_kayit_sayisi = 0
                st.rerun()

    else:
        c_form, c_excel = st.columns(2)
        with c_form:
            with st.form("manuel_form"):
                st.write("Elden Kayıt")
                bn, fa, ma, ti, sn = st.text_input("B.No"), st.text_input("Firma"), st.text_input("Marka"), st.text_input("Tip"), st.text_input("Şasi")
                if st.form_submit_button("Ekle"):
                    try:
                        with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
                            conn.cursor().execute("INSERT INTO denetimler (firma_adi, marka, arac_tipi, sasi_no, basvuru_no, durum, basvuru_tarihi, secim_tarihi, il) VALUES (?,?,?,?,?, 'Teste Gönderildi', ?, ?, ?)", (fa, ma, ti, sn, bn, datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il))
                            conn.commit()
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Bu şasi numarası sistemde mevcut!")
        
        with c_excel:
            up = st.file_uploader("Excel Yükle", type=['xlsx', 'csv'])
            if up and st.button("Sisteme Aktar"):
                try:
                    if up.name.endswith('.csv'):
                        df_ekle = pd.read_csv(up)
                    else:
                        df_ekle = pd.read_excel(up)
                    
                    sutun_haritasi = {
                        "BasvuruNo": "basvuru_no",
                        "Firma": "firma_adi",
                        "Marka": "marka",
                        "Araç Kategori": "arac_kategori",
                        "Tip": "arac_tipi",
                        "Varyant": "varyant",
                        "Versiyon": "versiyon",
                        "TicariAd": "ticari_ad",
                        "GtipNo": "gtip_no",
                        "Birim": "birim",
                        "Üretildiği Ülke": "uretim_ulkesi",
                        "Araç Sayısı": "arac_sayisi"
                    }
                    
                    df_ekle.columns = df_ekle.columns.str.strip()
                    df_ekle.rename(columns=sutun_haritasi, inplace=True)
                    
                    df_ekle['ekleyen_kullanici'] = st.session_state.kullanici_adi
                    if 'durum' not in df_ekle.columns:
                        df_ekle['durum'] = 'Şasi Bekliyor'
                    
                    def il_tahmin_et(birim_metni):
                        if pd.isna(birim_metni): return st.session_state.sorumlu_il
                        metin = str(birim_metni).upper()
                        if "ANKARA" in metin: return "Ankara"
                        elif "İSTANBUL" in metin or "ISTANBUL" in metin: return "İstanbul"
                        elif "İZMİR" in metin or "IZMIR" in metin: return "İzmir"
                        elif "BURSA" in metin: return "Bursa"
                        elif "KOCAELİ" in metin or "KOCAELI" in metin: return "Kocaeli"
                        return st.session_state.sorumlu_il 

                    if 'birim' in df_ekle.columns:
                        df_ekle['il'] = df_ekle['birim'].apply(il_tahmin_et)
                    elif 'il' not in df_ekle.columns:
                        df_ekle['il'] = st.session_state.sorumlu_il
                    
                    gecerli_sutunlar = ['basvuru_no', 'firma_adi', 'marka', 'arac_kategori', 'arac_tipi', 
                                        'varyant', 'versiyon', 'ticari_ad', 'gtip_no', 'birim', 'uretim_ulkesi', 
                                        'arac_sayisi', 'sasi_no', 'basvuru_tarihi', 'secim_tarihi', 'il', 'durum', 
                                        'notlar', 'guncelleme_tarihi', 'ekleyen_kullanici', 'silme_talebi', 'silme_nedeni']
                    
                    df_ekle = df_ekle[[col for col in df_ekle.columns if col in gecerli_sutunlar]]
                    
                    with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
                        mevcut_kayitlar = pd.read_sql_query("SELECT basvuru_no, firma_adi, marka, arac_tipi FROM denetimler", conn)
                    
                    mevcut_basvuru_listesi = mevcut_kayitlar['basvuru_no'].astype(str).tolist()
                    df_ekle['basvuru_no_str'] = df_ekle['basvuru_no'].astype(str)
                    
                    df_yeni = df_ekle[~df_ekle['basvuru_no_str'].isin(mevcut_basvuru_listesi)].copy()
                    df_yeni.drop(columns=['basvuru_no_str'], inplace=True)
                    atlanan_sayi = len(df_ekle) - len(df_yeni)
                    
                    if len(df_yeni) == 0:
                        st.warning("⚠️ Yüklediğiniz dosyadaki tüm kayıtlar zaten sistemde mevcut! Mükerrer kayıt engellendi.")
                    else:
                        cakisma_var = False
                        if not mevcut_kayitlar.empty:
                            mevcut_str = (mevcut_kayitlar['firma_adi'].astype(str) + mevcut_kayitlar['marka'].astype(str) + mevcut_kayitlar['arac_tipi'].astype(str)).str.lower().str.replace(" ", "")
                            yeni_str = (df_yeni['firma_adi'].astype(str) + df_yeni['marka'].astype(str) + df_yeni['arac_tipi'].astype(str)).str.lower().str.replace(" ", "")
                            
                            cakisma_var = yeni_str.isin(mevcut_str).any()
                        
                        if cakisma_var:
                            st.session_state.onay_bekleyen_excel_df = df_yeni
                            st.session_state.atlanan_kayit_sayisi = atlanan_sayi
                            st.rerun()
                        else:
                            excel_kaydet_ve_mail_at(df_yeni, atlanan_sayi)
                            
                except Exception as e:
                    st.error(f"Aktarım sırasında kritik bir hata oluştu: {e}")

if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("👑 Yönetici Paneli")
        
        co, cs = st.columns(2)
        with co:
            st.markdown(f"**Onay Bekleyen Üyeler ({b_onay})**")
            with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
                k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn)
            
            for _, r in k_df.iterrows():
                st.write(f"👤 {r['kullanici_adi']}")
                if st.button("Onayla", key=f"o_{r['id']}"):
                    with sqlite3.connect('tse_v4.db', check_same_thread=False) as c:
                        c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],))
                        c.commit()
                    st.rerun()
        with cs:
            st.markdown(f"**Silme Talepleri ({b_silme})**")
            for _, r in df[df['silme_talebi']==1].iterrows():
                st.write(f"🗑️ {r['sasi_no']}")
                if st.button("Kalıcı Sil", key=f"sil_{r['id']}"):
                    with sqlite3.connect('tse_v4.db', check_same_thread=False) as c:
                        c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],))
                        c.commit()
                    st.rerun()

        st.divider() 

        st.subheader("👥 Kullanıcı Yönetimi")
        
        with sqlite3.connect('tse_v4.db', check_same_thread=False) as conn:
            tum_kullanicilar_df = pd.read_sql_query("SELECT id, kullanici_adi, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar", conn)
        
        st.dataframe(tum_kullanicilar_df, use_container_width=True)

        c_yetki, c_kayit_sil, c_kullanici_sil = st.columns(3)
        
        with c_yetki:
            st.markdown("**Excel Yükleme Yetkisi Düzenle**")
            secili_kullanici = st.selectbox("Kullanıcı Seçin", tum_kullanicilar_df['kullanici_adi'].tolist(), key="yetki_kullanici")
            if secili_kullanici:
                mevcut_yetki = tum_kullanicilar_df[tum_kullanicilar_df['kullanici_adi'] == secili_kullanici]['excel_yukleme_yetkisi'].iloc[0]
                yeni_yetki = st.radio("Yetki Durumu:", [1, 0], index=0 if mevcut_yetki == 1 else 1, format_func=lambda x: "Yetkili (1)" if x == 1 else "Yetkisiz (0)")
                if st.button("Yetkiyi Güncelle"):
                    with sqlite3.connect('tse_v4.db', check_same_thread=False) as c:
                        c.execute("UPDATE kullanicilar SET excel_yukleme_yetkisi=? WHERE kullanici_adi=?", (yeni_yetki, secili_kullanici))
                        c.commit()
                    st.success(f"{secili_kullanici} yetkisi güncellendi.")
                    time.sleep(1); st.rerun()

        with c_kayit_sil:
            st.markdown("**Doğrudan Kayıt Silme**")
            st.info("⚠️ Silinen kayıtlar geri getirilemez.")
            silinecek_secim = st.selectbox("Silinecek Kaydı Seç (Şasi veya Başvuru No)", options=["Seçiniz..."] + (df['id'].astype(str) + " | Şasi: " + df['sasi_no'].fillna('-').astype(str) + " | Başvuru: " + df['basvuru_no'].fillna('-').astype(str)).tolist())
            if silinecek_secim != "Seçiniz..." and st.button("🚨 Kaydı Kalıcı Sil"):
                sil_id = int(silinecek_secim.split(" |")[0])
                with sqlite3.connect('tse_v4.db', check_same_thread=False) as c:
                    c.execute("DELETE FROM denetimler WHERE id=?", (sil_id,))
                    c.commit()
                st.success("Kayıt kalıcı olarak silindi.")
                time.sleep(1); st.rerun()
                
        with c_kullanici_sil:
            st.markdown("**Kullanıcı Hesabını Sil**")
            st.info("⚠️ Silinen kullanıcı geri getirilemez.")
            silinecek_kullanici = st.selectbox("Silinecek Kullanıcıyı Seçin", ["Seçiniz..."] + tum_kullanicilar_df['kullanici_adi'].tolist(), key="sil_kullanici_sec")
            
            if silinecek_kullanici != "Seçiniz..." and st.button("🚨 Kullanıcıyı Sil"):
                if silinecek_kullanici == st.session_state.kullanici_adi:
                    st.error("Kendi hesabınızı silemezsiniz!")
                else:
                    with sqlite3.connect('tse_v4.db', check_same_thread=False) as c:
                        c.execute("DELETE FROM kullanicilar WHERE kullanici_adi=?", (silinecek_kullanici,))
                        c.commit()
                    st.success(f"{silinecek_kullanici} kullanıcısı sistemden silindi.")
                    time.sleep(1); st.rerun()
