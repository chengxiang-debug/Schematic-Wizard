# -*- coding: utf-8 -*-
"""列出候选器件的引脚名/号/类型 + 库里自带封装，供 catalog 家族选型用。

用法: D:/KiCad/bin/python.exe test/sym_pins.py [关键字 ...]
"""
import io
import os
import re
import sys

sys.path.insert(0, "D:/AutoDraw/app/engine")
import engine as E

FP_RE = re.compile(r'property "Footprint" "([^"]*)"')


def default_fp(lib_id):
    lib, name = lib_id.split(":", 1)
    p = os.path.join(E.KICAD_SYM, lib + ".kicad_sym")
    if not os.path.exists(p):
        return ""
    txt = io.open(p, encoding="utf-8", errors="replace").read()
    i = txt.find('\n\t(symbol "%s"' % name)
    if i < 0:
        return ""
    m = FP_RE.search(txt[i:i + 4000])
    return m.group(1) if m else ""


GROUPS = {
    "ldo_fix": ["Regulator_Linear:AMS1117-1.8", "Regulator_Linear:AMS1117-3.3",
                "Regulator_Linear:LP2985-3.3", "Regulator_Linear:ME6211C33M5",
                "Regulator_Linear:XC6206PxxxMR", "Regulator_Linear:SPX3819M5-L-3-3",
                "Regulator_Linear:LP5907MFX-3.3", "Regulator_Linear:TPS7A0533PDBV"],
    "ldo_adj": ["Regulator_Linear:LM317LZ", "Regulator_Linear:LM317L_SO8",
                "Regulator_Linear:LM317_SOT-223", "Regulator_Linear:LM337L_TO92",
                "Regulator_Linear:LM1117DT-ADJ", "Regulator_Linear:AMS1117",
                "Regulator_Linear:RT9193??"],
    "buck": ["Regulator_Switching:LM2576T-ADJ", "Regulator_Switching:LM2576S-ADJ",
             "Regulator_Switching:LM2596S-ADJ", "Regulator_Switching:LM2576T-5",
             "Regulator_Switching:LM2576S-3.3", "Regulator_Switching:MC34063AD",
             "Regulator_Switching:TPS5430DDA", "Regulator_Switching:MP2303ADN"],
    "pump": ["Regulator_SwitchedCapacitor:LT1054", "Regulator_SwitchedCapacitor:ICL7660",
             "Regulator_SwitchedCapacitor:LTC1044",
             "Regulator_SwitchedCapacitor:TPS60403DBV"],
    "ref": ["Reference_Voltage:TL431D", "Reference_Voltage:TL431DBZ",
            "Device:Zener_diode", "Reference_Voltage:LM385-1.2"],
    "prot": ["Device:Polyfuse", "Device:D_TVS", "Device:D_Schottky", "Diode:SS34",
             "Diode:B540C", "Device:FerriteBead", "Device:Fuse",
             "Transistor_FET:AO3401A", "Transistor_FET:IRF9540N",
             "Interface_USB:USBLC6-2SC6", "Diode:PESD3V3S1UL"],
    "sw": ["Transistor_BJT:MMBT3904", "Transistor_BJT:MMBT3906",
           "Transistor_BJT:2N3904", "Transistor_BJT:SS8050", "Transistor_BJT:SS8550",
           "Transistor_BJT:TIP120", "Transistor_FET:2N7002", "Transistor_FET:BSS138",
           "Transistor_FET:IRF540N", "Transistor_Array:ULN2003A",
           "Device:Relay", "Device:Motor"],
    "opa": ["Amplifier_Operational:LM358", "Amplifier_Operational:LM324",
            "Amplifier_Operational:TL072", "Amplifier_Operational:NE5532",
            "Amplifier_Operational:OPA2340", "Comparator:LM393",
            "Comparator:LM2903", "Comparator:TL331", "Comparator:LM311",
            "Device:R_Potentiometer", "Device:R Trimmer"],
    "cur": ["Amplifier_Current:INA180A1", "Amplifier_Current:INA199xxDCK",
            "Sensor_Energy:INA219AxD", "Sensor_Energy:INA226",
            "Sensor_Current:ACS712xLCTR-05B", "Device:Shunt",
            "Device:R_Shunt"],
    "led": ["Driver_LED:BCR430UW6", "Driver_LED:AL8860MP",
            "Regulator_Linear:LM317L_SOT-89"],
    "mot": ["Driver_Motor:DRV8833PW", "Driver_Motor:A4950E", "Driver_Motor:L298N",
            "Driver_Motor:Pololu_Breakout_A4988", "Driver_Motor:TMC2209-LA"],
    "if": ["Interface_UART:MAX3232", "Interface_UART:MAX485E",
           "Interface_CAN_LIN:SN65HVD230", "Interface_USB:CH340C",
           "Interface_Expansion:PCF8574AP", "Interface_Expansion:MCP23017x-x-SO",
           "Interface_Expansion:PCF8591", "Analog_ADC:ADS1115IDGS",
           "Analog_ADC:HX711", "Memory_Flash:W25Q128JVS"],
    "clk": ["Timer_RTC:DS1307+", "Timer_RTC:PCF8563T", "Timer_RTC:DS3231M",
            "Device:Crystal", "Oscillator:SG-210STPC", "Device:Resonator"],
    "aud": ["Amplifier_Audio:LM386", "Amplifier_Audio:PAM8403D",
            "Amplifier_Audio:TDA1308", "Audio:MAX98357A"],
    "batt": ["Battery_Management:TP4056-42-ESOP8", "Battery_Management:DW01A",
             "Device:Battery", "Battery_Management:MC4051"],
    "sens": ["Device:Thermistor_NTC", "Device:R_Photo", "Device:D_Photo",
             "Device:Q_Photo_NPN", "Sensor_Temperature:LM35D",
             "Sensor_Temperature:DS18B20", "Device:HallGenerator",
             "Transducer_Rotation:?", "Sensor_Motion:MPU-9250"],
}

only = sys.argv[1:]
for g, ids in GROUPS.items():
    if only and g not in only:
        continue
    print("\n#### %s" % g)
    for lid in ids:
        try:
            geo = E.lib_geometry(lid)
        except Exception as e:
            print("  MISS %-44s %s" % (lid, str(e)[:50]))
            continue
        pins = {}
        for p in geo[1]:
            pins.setdefault(p["num"], []).append((p["name"], p["type"]))
        d = E.lib_pins(lid, 0)
        print("  %-44s fp=%s" % (lid, default_fp(lid) or "-"))
        for num in sorted(pins, key=lambda t: (len(t), t)):
            dx, dy = d[num][0], d[num][1]
            for nm, ty in pins[num]:
                print("      %-3s %-14s %-13s (%g,%g)" % (num, nm, ty, dx, dy))
