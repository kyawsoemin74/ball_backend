import configparser
from alembic.config import Config
from alembic import command

# Python 3.14 Error မတက်အောင် configparser ကို Raw ပြောင်းပေးခြင်း
cfg = Config("alembic.ini")
cfg.file_config = configparser.RawConfigParser()
cfg.file_config.read("alembic.ini")

# ၁။ အရင်ဆုံး Alembic ကို ဗားရှင်းဟောင်း (0001) ကို ပြန်ဆုတ်ခိုင်းလိုက်မယ် (Stamp Back)
print("Stamping back to 0001_initial...")
command.stamp(cfg, "0001_initial")

# ၂။ ပြီးမှ users table ဆောက်ဖို့ အတင်း Upgrade ပြန်လုပ်ခိုင်းမယ် (Force Upgrade)
print("Upgrading to head (Creating users table)...")
# အတင်းနောက်ပြန်မဆုတ်ဘဲ လက်ရှိရှိနေတဲ့ အခြေအနေကနေ ရှေ့ကိုပဲ သွားတာ ပိုကောင်းပါတယ်
print("Upgrading to latest migration head...")
command.upgrade(cfg, "head")

print("Done! Check your database now.")