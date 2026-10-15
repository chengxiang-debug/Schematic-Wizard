# -*- coding: utf-8 -*-
"""一次性探测：这些器件名在本机 KiCad 库里有没有、引脚分布长什么样。"""
import sys

sys.path.insert(0, "D:/AutoDraw/app/engine")
import engine as E

IDS = [
    # 线性稳压
    "Regulator_Linear:AMS1117-1.5", "Regulator_Linear:AMS1117-1.8",
    "Regulator_Linear:AMS1117-2.5", "Regulator_Linear:AMS1117-3.3",
    "Regulator_Linear:AMS1117-5.0", "Regulator_Linear:AMS1117-ADJ",
    "Regulator_Linear:LM1117-3.3", "Regulator_Linear:XC6206P332MR",
    "Regulator_Linear:TLV75509PDBVRQ1", "Regulator_Linear:LM317LZ",
    "Regulator_Linear:LM317TZ", "Regulator_Linear:NCP702BMX150G",
    "Regulator_Linear:LP2985-33DBVR", "Regulator_Linear:RT9193-33GB",
    # 开关稳压
    "Regulator_Switching:LM2576T-ADJ", "Regulator_Switching:LM2576T-5.0",
    "Regulator_Switching:LM2596S-ADJ", "Regulator_Switching:LM2596T-ADJ",
    "Regulator_Switching:MC34063AP1G", "Regulator_Switching:TPS5430DDAR",
    "Regulator_Switching:XL6009E1", "Regulator_Switching:MP1584EN",
    # 电荷泵
    "Regulator_SwitchedCapacitor:LT1054", "Regulator_SwitchedCapacitor:ICL7660",
    "Regulator_SwitchedCapacitor:LTC1044CN", "Regulator_SwitchedCapacitor:TPS60403DBVT",
    # 基准
    "Reference_Voltage:TL431ACDBZ", "Reference_Voltage:TL431A",
    "Reference_Voltage:LM4040D30ILT", "Device:Zener",
    # 三极管 / MOS
    "Transistor_BJT:MMBT3904", "Transistor_BJT:MMBT3906",
    "Transistor_BJT:2N3904", "Transistor_BJT:2N3906",
    "Transistor_BJT:SS8050", "Transistor_BJT:SS8550",
    "Transistor_BJT:TIP120", "Transistor_BJT:BC547",
    "Transistor_FET:2N7002", "Transistor_FET:BSS138",
    "Transistor_FET:IRF540N", "Transistor_FET:AO3401A",
    "Transistor_FET:SI2301DDS", "Transistor_FET:IRLML6344",
    "Transistor_FET:IRF9540N",
    # 阵列 / 驱动
    "Transistor_Array:ULN2003A", "Driver_Motor:DRV8833PWPR",
    "Driver_Motor:A4950EDTR-T", "Driver_Motor:L298N",
    "Driver_Motor:TMC2209-LA", "Driver_LED:BCR430UW6EG",
    "Driver_LED:AL8860MP", "Driver_LED:PCA9685BS",
    # 运放 / 比较器
    "Amplifier_Operational:LM358", "Amplifier_Operational:LM324",
    "Amplifier_Operational:TL072", "Amplifier_Operational:OPA2134PA",
    "Amplifier_Operational:MCP6002", "Comparator:LM393",
    "Comparator:TL331BDCKR", "Comparator:LM311N",
    # 电流检测
    "Amplifier_Current:INA180A1DBVR", "Amplifier_Current:INA199A1IDCKR",
    "Sensor_Energy:INA219AIDGSR", "Amplifier_Current:MAX44285F",
    # 接口
    "Interface_UART:MAX3232CPC", "Interface_UART:MAX485CPE",
    "Interface_CAN_LIN:SN65HVD230D", "Interface_USB:CH340C",
    "Interface_Expansion:PCF857AP", "Interface_Expansion:PCA9554APW",
    "Memory_Flash:W25Q128JVSIQ",
    # 时钟 / RTC
    "Timer_RTC:DS1307+", "Timer_RTC:PCF8563T",
    "Oscillator:Si5351A-B-GM", "Device:Crystal",
    # 音频 / 传感
    "Amplifier_Audio:LM386N-1", "Amplifier_Audio:PAM8403D",
    "Sensor_Optical:LTR-301ALS-01", "Transducer_Hall:A1120",
    "Sensor_Temperature:LM35D", "Interface:ADS1115IDGS",
    "Analog_ADC:HX711", "Audio:MAX98357A",
    # 分立
    "Device:D_Schottky", "Device:D_TVS", "Device:FerriteBead",
    "Device:L", "Device:R_Potentiometer", "Device:C_Polarized",
    "Device:CP", "Device:D", "Device:LED", "Device:Buzzer",
    "Device:Crystal", "Device:NTC_Thermistor", "Device:Fuse",
    "Device:Q_NPN_BEC", "Connector:Barrel_Jack",
    "Regulator_Linear:RT9013-33GB", "Regulator_Linear:LP5907-3.3",
]

for lib_id in IDS:
    try:
        pins = E.lib_pins(lib_id)
    except Exception as e:
        print("MISS  %-46s %s" % (lib_id, str(e)[:60]))
        continue
    try:
        g = E.lib_geometry(lib_id)
        names = {p["num"]: p["name"] for p in g[1]}
    except Exception:
        names = {}
    desc = " ".join("%s:%s%s%s" % (n, names.get(n, "?"),
                                   "(%g,%g)" % (d[0], d[1]), d[2][:2])
                    for n, d in sorted(pins.items(),
                                       key=lambda kv: (len(kv[0]), kv[0])))
    print("OK    %-46s [%s]" % (lib_id, desc))
