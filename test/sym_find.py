# -*- coding: utf-8 -*-
"""在 KiCad 符号库中按关键字找真实的器件名（避免凭印象猜后缀）。"""
import glob
import os
import re
import sys

KEYS = [a for a in sys.argv[1:]] or [
    "TL431", "LM431", "LM317", "LM337", "XC6206", "RT9013", "LP5907",
    "LP2985", "NCP702", "AMS1117", "LM1117", "TPS7A", "SPX", "ME6211",
    "MC34063", "XL6009", "MP1584", "LM2576", "LM2596", "TPS54", "SK632",
    "LT1054", "ICL7660", "LTC1044", "TPS60",
    "MAX3232", "MAX485", "SN65HVD", "MCP2515", "CH340", "CP210", "FT232",
    "LM386", "PAM84", "TDA", "NE5532", "LM4862",
    "INA18", "INA19", "INA226", "INA219", "MAX4", "ACS712",
    "PCF8574", "PCF8575", "PCA9554", "PCA9536", "MCP23017", "W25Q", "GD25",
    "ADS1115", "ADS1015", "PCF8591", "HX711",
    "DRV8833", "A4950", "A4988", "L298", "TMC22", "L9110", "ULN200",
    "BCR430", "PT4115", "AL886", "TPIC6B",
    "TL331", "LM393", "LM2903", "LM311", "TLV35",
    "NTC", "TDK", "Photo", "LDR", "Hall", "A112", "SS49E", "US588",
    "DS18B20", "DS1307", "PCF8563", "DS3231", "MCP794",
    "Si5351", "ASRV", "ABS07", "SM8",
    "MMBT", "BC84", "BC85", "SS80", "SS85", "2N7002", "BSS138", "AO34",
    "IRF", "IRL", "SI23", "FDD", "IPB",
    "TP4056", "DW01", "HW-193", "BQ24", "SY8089", "MP23",
    "TVS", "SMBJ", "P6KE", "SMDJ", "PESD", "USBLC",
]

root = r"D:/KiCad/share/kicad/symbols"
found = {}
for path in sorted(glob.glob(root + "/*.kicad_sym")):
    lib = os.path.basename(path)[:-10]
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'^\t\(symbol "([^"]+)"', line)
            if not m:
                continue
            name = m.group(1)
            if "_0_" in name or "_1_" in name or "_2_" in name:
                continue
            up = name.upper()
            for k in KEYS:
                if k.upper() in up:
                    found.setdefault(k, []).append("%s:%s" % (lib, name))
for k in KEYS:
    v = found.get(k, [])
    print("== %s (%d)" % (k, len(v)))
    for x in v[:16]:
        print("   " + x)
