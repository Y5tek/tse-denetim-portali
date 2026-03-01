import sqlite3
import hashlib

# 1. Adım: Yeni şifrenizi belirliyoruz ve şifreliyoruz (hash)
kullanici_adiniz = "admin" # Kendi admin kullanıcı adınız neyse buraya yazın (örneğin: "yonetici")
yeni_sifre = "admin123"    # Yeni şifreniz bu olacak
hashli_yeni_sifre = hashlib.sha256(yeni_sifre.encode('utf-8')).hexdigest()

try:
    # 2. Adım: Veritabanına bağlanıp şifreyi güncelliyoruz
    with sqlite3.connect('tse_v4.db') as conn:
        cursor = conn.cursor()
        
        # Kullanıcıyı bul ve şifresini güncelle
        cursor.execute("UPDATE kullanicilar SET sifre=? WHERE kullanici_adi=?", (hashli_yeni_sifre, kullanici_adiniz))
        
        # Etkilenen satır sayısını kontrol edelim
        if cursor.rowcount > 0:
            print(f"✅ Harika! '{kullanici_adiniz}' kullanıcısının şifresi başarıyla '{yeni_sifre}' olarak sıfırlandı.")
            print("Artık portala bu yeni şifreyle giriş yapabilirsiniz.")
        else:
            print(f"❌ Hata: '{kullanici_adiniz}' adında bir kullanıcı veritabanında bulunamadı. Lütfen kullanıcı adını kontrol edin.")
            
        conn.commit()

except Exception as e:
    print(f"Bir hata oluştu: {e}")
