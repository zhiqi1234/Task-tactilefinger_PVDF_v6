#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STM32 Firmware Diagnostic Tool — PiezoTac 32CH (ADS131M0x ×4)

读取并分析 main.c / queue.c / ads131m0x.c / hal.c，自动检测已知 Bug、
输出严重等级、根因解释和修复建议。

用法:
    python diagnostic.py [--verbose] [--report report.txt]

依赖: Python 3.8+ (无第三方库依赖)
"""

import re
import os
import sys
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


# ──────────────────────────────────────────
#  数据模型
# ──────────────────────────────────────────

class Severity(Enum):
    CRITICAL = "CRITICAL"   # 必定导致系统失效
    HIGH = "HIGH"           # 大概率导致数据异常
    MEDIUM = "MEDIUM"       # 特定条件下异常
    LOW = "LOW"             # 隐患 / 不良实践


@dataclass
class BugReport:
    id: str
    title: str
    severity: Severity
    file: str
    lines: str                     # e.g. "172-174"
    problem: str                   # 现象 / 根因
    impact: str                    # 实际影响
    fix: str                       # 修复建议

    def __str__(self):
        sev_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}
        icon = sev_icon.get(self.severity.value, "⚪")
        return (
            f"\n{'─' * 78}\n"
            f" {icon} [{self.severity.value}] BUG-{self.id}: {self.title}\n"
            f"    文件: {self.file}:{self.lines}\n"
            f"    问题: {self.problem}\n"
            f"    影响: {self.impact}\n"
            f"    修复: {self.fix}\n"
            f"{'─' * 78}"
        )


@dataclass
class TimingReport:
    """SPI 读取时序 vs UART 发送时序的对比分析"""
    spi_read_4chips_us: float      # 读完 4 颗芯片一个周期所需的 SPI 时间
    uart_frame_us: float           # 发送一帧 (29 bytes @ 921600 baud) 所需时间
    uart_4frames_us: float         # 4 帧总发送时间
    read_send_ratio: float         # 读取速率 / 发送速率
    drdy_period_us: float          # DRDY 周期 (OSR=16384, fMOD=4.096MHz)
    drdy_low_duration_us: float    # DRDY 低电平持续时长
    queue_capacity: int            # 队列容量
    queue_fill_time_ms: float      # 队列填满所需时间 (紧循环模式下)

    def __str__(self):
        return (
            f"\n{'═' * 60}\n"
            f"  ⏱  时序分析\n"
            f"{'═' * 60}\n"
            f"  SPI 读 4 芯片 / 轮        : {self.spi_read_4chips_us:>8.1f} µs\n"
            f"  UART 单帧发送 (29 bytes)  : {self.uart_frame_us:>8.1f} µs\n"
            f"  UART 4 帧发送             : {self.uart_4frames_us:>8.1f} µs\n"
            f"  DRDY 周期                 : {self.drdy_period_us:>8.1f} µs\n"
            f"  DRDY 低电平持续           : {self.drdy_low_duration_us:>8.1f} µs\n"
            f"  DRDY 低电平占比           : {self.drdy_low_duration_us/self.drdy_period_us*100:>8.1f}%\n"
            f"  {'─' * 50}\n"
            f"  读取 / 发送 速率比        : {self.read_send_ratio:>8.1f}x\n"
            f"  队列填满时间 (紧循环)     : {self.queue_fill_time_ms:>8.1f} ms\n"
            f"{'═' * 60}"
        )


# ──────────────────────────────────────────
#  文件读取 & 源码解析
# ──────────────────────────────────────────

class SourceFile:
    """单文件源码解析器"""
    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self.lines: List[str] = []
        self.content = ""
        self._load()

    def _load(self):
        with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
            self.content = f.read()
        self.lines = self.content.split("\n")

    def find_line_with(self, pattern: str) -> Optional[int]:
        """返回第一个匹配行的行号 (1-indexed)，无匹配返回 None"""
        for i, line in enumerate(self.lines, 1):
            if pattern in line:
                return i
        return None

    def find_line_re(self, regex: str) -> Optional[int]:
        """正则匹配，返回第一个匹配行号"""
        pat = re.compile(regex)
        for i, line in enumerate(self.lines, 1):
            if pat.search(line):
                return i
        return None

    def count_occurrences(self, pattern: str) -> int:
        return self.content.count(pattern)

    def has_pattern(self, pattern: str) -> bool:
        return pattern in self.content

    def has_regex(self, regex: str) -> bool:
        return bool(re.search(regex, self.content))

    def get_lines_range(self, start: int, end: int) -> str:
        return "\n".join(self.lines[start-1:end])


class SourceSet:
    """管理所有源文件"""
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.main = SourceFile(os.path.join(base_dir, "Core", "Src", "main.c"))
        self.queue = SourceFile(os.path.join(base_dir, "Core", "Src", "queue.c"))
        self.ads = SourceFile(os.path.join(base_dir, "Core", "Src", "ads131m0x.c"))
        self.hal = SourceFile(os.path.join(base_dir, "Core", "Src", "hal.c"))
        self.spi = SourceFile(os.path.join(base_dir, "Core", "Src", "spi.c"))
        self.queue_h = SourceFile(os.path.join(base_dir, "Core", "Inc", "queue.h"))
        self.ads_h = SourceFile(os.path.join(base_dir, "Core", "Inc", "ads131m0x.h"))
        self.hal_h = SourceFile(os.path.join(base_dir, "Core", "Inc", "hal.h"))


# ──────────────────────────────────────────
#  Bug 检测器
# ──────────────────────────────────────────

class BugDetector:
    """核心 Bug 检测逻辑"""

    def __init__(self, src: SourceSet):
        self.src = src
        self.bugs: List[BugReport] = []

    def detect_all(self) -> List[BugReport]:
        self._check_drdy_tight_loop()
        self._check_queue_silent_drop()
        self._check_dequeue_uninit_return()
        self._check_missing_toggle_sync()
        self._check_setcs_pin_truncation()
        self._check_drdy_watchdog_unused()
        self._check_spi_polling_bottleneck()
        self._check_no_frame_timing_guard()
        self._check_drdy_watchdog_implementation()
        return self.bugs

    # ── Bug 1: DRDY Level Mode + GPIO Polling = Tight Loop ──────────

    def _check_drdy_tight_loop(self) -> None:
        f = self.src.main
        pattern = "data_ready_flag == 1 || HAL_GPIO_ReadPin"
        line = f.find_line_with(pattern)
        if line is None:
            return

        has_watchdog_check = "drdy_watchdog" in f.content and "++drdy_watchdog" in f.content
        has_only_data_ready = re.search(
            r"if\s*\(\s*data_ready_flag\s*==\s*1\s*\)\s*$",
            f.lines[line-1].strip() if line <= len(f.lines) else "",
        ) is not None

        # 检测 DRDY_FMT 是否为 pulse 还是 level
        drdy_fmt_pulse = self.src.ads_h.has_pattern("DRDY_FMT_PULSE") and \
            not self.src.ads_h.content.count("// #define DRDY_FMT_PULSE") or \
            not self.src.ads_h.has_pattern("//#define DRDY_FMT_PULSE")

        # 如果已经注释掉了 DRDY_FMT_PULSE，则是电平模式
        level_mode = not drdy_fmt_pulse or \
                     "//#define DRDY_FMT_PULSE" in self.src.ads_h.content or \
                     "// #define DRDY_FMT_PULSE" in self.src.ads_h.content

        if has_only_data_ready and has_watchdog_check:
            # 已修复
            return

        if not has_watchdog_check:
            self.bugs.append(BugReport(
                id="MAIN_01",
                title="DRDY 电平模式 + GPIO 轮询 组合导致紧循环重复读取",
                severity=Severity.CRITICAL,
                file="Core/Src/main.c",
                lines=str(line),
                problem=(
                    "主循环检测条件使用了 OR 逻辑：\n"
                    "    if (data_ready_flag == 1 || HAL_GPIO_ReadPin(DRDY) == GPIO_PIN_RESET)\n"
                    f"DRDY 在电平模式下约 99.99% 时间保持低电平，因此 HAL_GPIO_ReadPin 几乎恒返回 RESET，\n"
                    "导致条件几乎恒为真。读完 4 芯片后回到 while(1) 顶部，立即再次读取同一批数据。"
                ),
                impact=(
                    "1) SPI 读 4 芯片 ~50µs，而 UART 发 4 帧需 ~1.26ms\n"
                    "2) 读取速率是发送速率的 ~25 倍\n"
                    "3) 队列 (capacity=50) 瞬间填满 → enqueue() 静默丢弃所有数据\n"
                    "4) 排查表观异常：\n"
                    "   - receiveData[i][0]==0xFF → memset 清零 → \"电平不动\"\n"
                    "   - 队列满/不满抖动 → 新旧数据交替 → \"高频跳动\"\n"
                    "   - 复位后队列空 → \"复位后√\" → 迅速恶化"
                ),
                fix=(
                    "改为仅 EXTI 驱动 + 看门狗备份：\n"
                    "  if (data_ready_flag == 1) {           // 仅 EXTI 触发\n"
                    "      data_ready_flag = 0;\n"
                    "      drdy_watchdog = 0;\n"
                    "      // 依次读取 4 芯片...\n"
                    "  } else if (++drdy_watchdog > 5000000) { // ~50ms 超时\n"
                    "      drdy_watchdog = 0;\n"
                    "      if (HAL_GPIO_ReadPin(DRDY) == GPIO_PIN_RESET) {\n"
                    "          // GPIO 电平备份读取\n"
                    "      }\n"
                    "  }"
                ),
            ))
        else:
            self.bugs.append(BugReport(
                id="MAIN_01",
                title="DRDY 电平模式 + GPIO 轮询 组合导致紧循环重复读取 (已部分修复但 watchdog 未正确使用)",
                severity=Severity.HIGH,
                file="Core/Src/main.c",
                lines=str(line),
                problem=(
                    "drdy_watchdog 变量已声明但 if 条件中仍包含 GPIO 电平轮询作为 OR 条件之一。\n"
                    "DRDY 电平模式下低电平占 ~99.99%，条件几乎恒为真。"
                ),
                impact="仍可能产生紧循环重复读取，队列快速填满并丢弃数据。",
                fix="移除 GPIO 电平轮询作为 OR 条件，改为单独的 EXTI-only 分支 + else-if watchdog 备份。",
            ))

    # ── Bug 2: 队列满时静默丢弃 ────────────────────────────────────

    def _check_queue_silent_drop(self) -> None:
        f = self.src.queue
        # 查找 enqueue 函数中的早期 return
        lines_found = []
        in_enqueue = False
        for i, line in enumerate(f.lines, 1):
            if "void enqueue" in line or "enqueue(Queue" in line:
                in_enqueue = True
            if in_enqueue:
                if "if (isFull(queue))" in line:
                    # 检查下一行是否只是 return
                    next_line = f.lines[i].strip() if i < len(f.lines) else ""
                    if "return" in next_line:
                        lines_found.append(i)
                if "return" in line and "isFull" not in line and lines_found:
                    # 找到了对应的 return
                    pass
                if "}" in line and in_enqueue and not lines_found:
                    in_enqueue = False
                if "}" in line and lines_found:
                    in_enqueue = False

        # 用更可靠的方式检测
        enqueue_func = re.search(
            r"void enqueue\(.*?\)\s*\{([^}]*?)\}",
            f.content, re.DOTALL
        )

        has_overflow_counter = "overrun" in f.content or "overflow" in f.content or \
                               "queue_overrun" in self.src.main.content

        is_silent = False
        if enqueue_func:
            body = enqueue_func.group(1)
            if "if (isFull" in body and "return;" in body:
                is_silent = True

        if is_silent and not has_overflow_counter:
            self.bugs.append(BugReport(
                id="QUEUE_01",
                title="enqueue() 队列满时静默丢弃，无任何溢出计数器或告警",
                severity=Severity.CRITICAL,
                file="Core/Src/queue.c",
                lines="38-40",
                problem=(
                    "enqueue() 函数中：\n"
                    "    if (isFull(queue)) {\n"
                    "        return;  // 静默丢弃，不留任何痕迹\n"
                    "    }\n"
                    "没有任何计数器记录溢出次数，上位机无法区分 '无数据' 和 '数据被丢弃'。"
                ),
                impact=(
                    "1) 上位机完全不知道发生了数据丢失\n"
                    "2) 即使添加了 DRDY 看门狗，在极限条件下仍可能溢出\n"
                    "3) 诊断困难：无法判断是传感器未输出还是数据被丢弃"
                ),
                fix=(
                    "方案 A (推荐)：队列满时覆盖最旧数据（环形覆盖）\n"
                    "    if (isFull(queue)) {\n"
                    "        queue->front = (queue->front + 1) % queue->capacity;\n"
                    "        queue->size--;\n"
                    "        queue->overrun_count++;  // 新增溢出计数器\n"
                    "    }\n"
                    "\n"
                    "方案 B：增加 overrun_count 计数器并通过 UART 发送或控制 LED"
                ),
            ))

    # ── Bug 3: dequeue() 空队列返回未初始化数据 ────────────────────

    def _check_dequeue_uninit_return(self) -> None:
        f = self.src.queue
        dequeue_func = re.search(
            r"QueueElement dequeue\(.*?\)\s*\{(.*?)\n\}",
            f.content, re.DOTALL
        )
        if not dequeue_func:
            return

        body = dequeue_func.group(1)
        has_assert = "assert" in body
        has_uninit_return = "QueueElement item" in body and "return item" in body

        # 检查 main.c 是否在 dequeue 前调用了 isEmpty
        main_calls_dequeue = self.src.main.count_occurrences("dequeue(")
        main_calls_isempty = self.src.main.count_occurrences("isEmpty(")

        if has_uninit_return:
            severity = Severity.LOW
            if main_calls_dequeue > main_calls_isempty:
                severity = Severity.HIGH  # main.c 可能在未检查 isEmpty 的情况下调用 dequeue

            self.bugs.append(BugReport(
                id="QUEUE_02",
                title="dequeue() 空队列时返回未初始化的栈变量",
                severity=severity,
                file="Core/Src/queue.c",
                lines="51-55",
                problem=(
                    "dequeue() 中声明了局部变量 QueueElement item，但未初始化：\n"
                    "    QueueElement item;      // 栈上的随机值\n"
                    "    if (isEmpty(queue)) {\n"
                    "        return item;        // 返回垃圾数据\n"
                    "    }\n"
                    f"main.c 中 dequeue() 调用次数={main_calls_dequeue}, isEmpty() 检查次数={main_calls_isempty}"
                ),
                impact=(
                    "如果调用方忘了先检查 isEmpty()，会收到垃圾数据帧并发送给上位机。\n"
                    "目前 main.c 在 dequeue 前已检查 isEmpty()，但函数本身的接口是不安全的。"
                ),
                fix=(
                    "添加 memset 初始化：\n"
                    "    QueueElement item;\n"
                    "    memset(&item, 0, sizeof(QueueElement));  // 初始化\n"
                    "    if (isEmpty(queue)) return item;"
                ),
            ))

    # ── Bug 4: adcStartup() 缺少 toggleSYNC() ───────────────────────

    def _check_missing_toggle_sync(self) -> None:
        f = self.src.ads
        # 查找 adcStartup 函数
        startup_match = re.search(r"void adcStartup\(void\)\s*\{(.*?)\n\}", f.content, re.DOTALL)
        if not startup_match:
            return

        body = startup_match.group(1)
        has_toggle_sync = "toggleSYNC" in body

        # 检查是否有 4 芯片循环
        has_chip_loop = "for (int chip" in body or "for (int i" in body

        if not has_toggle_sync and has_chip_loop:
            # 找到最后一个 writeSingleRegister 的行号
            last_write = None
            for i, line in enumerate(f.lines, 1):
                if "writeSingleRegister" in line and "CLOCK" in line or "writeSingleRegister" in line and "MODE" in line:
                    last_write = i

            if last_write:
                self.bugs.append(BugReport(
                    id="ADS_01",
                    title="adcStartup() 配置 4 颗芯片后缺少 toggleSYNC() 同步脉冲",
                    severity=Severity.MEDIUM,
                    file="Core/Src/ads131m0x.c",
                    lines=f"{last_write}-{last_write+1}",
                    problem=(
                        "adcStartup() 中依次对 4 颗芯片逐个写 CLOCK/MODE 寄存器，"
                        "但写完所有芯片后未调用 toggleSYNC()。\n"
                        "虽然所有芯片共享 RESET 引脚 (复位时同步) 和 CLKIN (同时钟源)，"
                        "但顺序写寄存器导致各芯片的配置生效时刻存在微小偏差。"
                    ),
                    impact=(
                        "1) 4 颗芯片的数字滤波器启动时刻存在微小偏差\n"
                        "2) 可能导致各芯片的采样窗口不完全对齐\n"
                        "3) 在需要精确同步的多传感器应用中引入通道间相位差"
                    ),
                    fix=(
                        "在 adcStartup() 末尾 for 循环结束后添加：\n"
                        "    toggleSYNC();  // 同步所有芯片的转换启动时刻\n"
                    ),
                ))

    # ── Bug 5: setCS() Pin 值截断 ───────────────────────────────────

    def _check_setcs_pin_truncation(self) -> None:
        f = self.src.hal
        # 找到 setCS 函数
        setcs_match = re.search(
            r"void setCS\(const bool state\)\s*\{(.*?)\n\}",
            f.content, re.DOTALL
        )
        if not setcs_match:
            return

        body = setcs_match.group(1)
        has_truncation = "uint8_t value = (uint8_t)" in body and "GPIO_Pin" in body

        if has_truncation:
            # 检查哪些 CS 引脚会受影响
            # PB11 (CS1) = 0x0800, PB10 (CS2) = 0x0400 → 截断为 0x00
            # PC6 (CS3) = 0x0040, PC7 (CS4) = 0x0080 → 保留
            self.bugs.append(BugReport(
                id="HAL_01",
                title="setCS() 使用 uint8_t 截断 GPIO_Pin 值，导致 CS1/CS2 无法拉高",
                severity=Severity.HIGH,
                file="Core/Src/hal.c",
                lines="176-180",
                problem=(
                    "setCS() 的实现存在类型截断问题：\n"
                    "    uint8_t value = (uint8_t) (state ? GPIO_Pin : 0);\n"
                    "    HAL_GPIO_WritePin(Port, Pin, value);\n\n"
                    "受影响引脚：\n"
                    "  CS1 (PB11) : GPIO_Pin = 0x0800 → (uint8_t) = 0x00 → 永远拉不高\n"
                    "  CS2 (PB10) : GPIO_Pin = 0x0400 → (uint8_t) = 0x00 → 永远拉不高\n"
                    "  CS3 (PC6)  : GPIO_Pin = 0x0040 → (uint8_t) = 0x40 → 正常\n"
                    "  CS4 (PC7)  : GPIO_Pin = 0x0080 → (uint8_t) = 0x80 → 正常\n\n"
                    "HAL_GPIO_WritePin 的 PinState 参数期望 GPIO_PIN_RESET(0) 或 GPIO_PIN_SET(1)，\n"
                    "Pin 编号不等于 Pin 状态值。"
                ),
                impact=(
                    "1) adcStartup() 中通过 setCS() 配置 CS1/CS2 的寄存器时，CS 永远无法拉高\n"
                    "2) CS1/CS2 的 SPI 通信可能异常（CS 一直保持低电平）\n"
                    "3) 这解释了测试结果中 CS1 对应的 ADC \"电平不动\" 的一致性异常\n"
                    "4) 注：主循环中的读取使用 HAL_GPIO_WritePin 直接操作，不受此 Bug 影响\n"
                    "   （但在 adcStartup 初始化阶段已造成寄存器写入失败）"
                ),
                fix=(
                    "改为使用 GPIO_PIN_SET / GPIO_PIN_RESET：\n"
                    "    GPIO_PinState value = state ? GPIO_PIN_SET : GPIO_PIN_RESET;\n"
                    "    HAL_GPIO_WritePin(csConfig[currentCSIndex].GPIO_Port,\n"
                    "                      csConfig[currentCSIndex].GPIO_Pin, value);"
                ),
            ))

    # ── Bug 6: drdy_watchdog 声明但未使用 ───────────────────────────

    def _check_drdy_watchdog_unused(self) -> None:
        f = self.src.main
        if "drdy_watchdog" not in f.content:
            return

        # 检查是否只在声明处出现，而从未在循环中使用
        declarations = f.content.count("drdy_watchdog")
        # 至少应该在声明 (1次) + while 循环中 (>=1次) 出现
        in_while_loop = False
        in_while = False
        for line in f.lines:
            if "while (1)" in line or "while(1)" in line:
                in_while = True
            if in_while and "drdy_watchdog" in line:
                in_while_loop = True
                break

        if declarations >= 1 and not in_while_loop:
            self.bugs.append(BugReport(
                id="MAIN_02",
                title="drdy_watchdog 变量已声明但从未在 while(1) 循环中使用",
                severity=Severity.MEDIUM,
                file="Core/Src/main.c",
                lines="89",
                problem=(
                    "main.c:89 声明了 static uint32_t drdy_watchdog = 0，\n"
                    "但在 while(1) 主循环中未对其进行递增或检查。\n"
                    "该变量用于在 EXTI 中断丢失时提供 GPIO 电平备份检测。"
                ),
                impact=(
                    "1) 当前的 GPIO 轮询作为 OR 条件之一，而非仅作为备份\n"
                    "2) 如果改为纯 EXTI 驱动模式后，缺少 watchdog 会导致 EXTI 丢失时系统永久卡死\n"
                    "3) 变量白白占用内存，且容易误导后续维护者"
                ),
                fix=(
                    "在 while(1) 循环中添加 watchdog 逻辑：\n"
                    "  if (data_ready_flag == 1) {\n"
                    "      data_ready_flag = 0;\n"
                    "      drdy_watchdog = 0;  // 重置看门狗\n"
                    "      // read 4 chips...\n"
                    "  } else {\n"
                    "      if (++drdy_watchdog > 5000000) {  // ~50ms\n"
                    "          drdy_watchdog = 0;\n"
                    "          if (HAL_GPIO_ReadPin(DRDY) == GPIO_PIN_RESET) {\n"
                    "              // GPIO backup read\n"
                    "          }\n"
                    "      }\n"
                    "  }"
                ),
            ))

    # ── Bug 7: SPI 软件轮询瓶颈 (SPI HAL 阻塞 + 无 DMA) ───────────

    def _check_spi_polling_bottleneck(self) -> None:
        f = self.src.main
        uses_polling_spi = "HAL_SPI_Receive(&hspi2" in f.content and \
                           "HAL_SPI_Receive_DMA" not in f.content and \
                           "HAL_SPI_Receive_IT" not in f.content

        if uses_polling_spi:
            self.bugs.append(BugReport(
                id="MAIN_03",
                title="SPI 数据读取使用阻塞式 HAL_SPI_Receive()，无 DMA 加速",
                severity=Severity.LOW,
                file="Core/Src/main.c",
                lines="184",
                problem=(
                    "主循环使用 HAL_SPI_Receive(&hspi2, receiveData[i], 27, 100) 进行阻塞式读取。\n"
                    "虽然 SPI2 的 DMA (RX: DMA1_Channel4, TX: DMA1_Channel5) 已初始化，\n"
                    "但未用于实际数据读取，CPU 在每次 SPI 传输期间被阻塞。"
                ),
                impact=(
                    "1) SPI 读 4 芯片耗时 ~50µs 期间 CPU 完全被占用\n"
                    "2) 如果后续通过空闲时间做数据处理 (DSP/滤波)，性能不足\n"
                    "3) 当前配置下影响不大 (UART 是瓶颈)，但限制了未来扩展"
                ),
                fix=(
                    "可选用 DMA 方式读取：\n"
                    "  HAL_SPI_Receive_DMA(&hspi2, receiveData[i], 27);\n"
                    "但需要重构为状态机驱动（等待 DMA 完成回调），复杂度较高。"
                ),
            ))

    # ── Bug 8: 没有数据帧间时间保护 ─────────────────────────────────

    def _check_no_frame_timing_guard(self) -> None:
        f = self.src.main
        has_delay = "HAL_Delay(" in f.content
        has_tick_check = "HAL_GetTick" in f.content

        if not has_delay and not has_tick_check:
            self.bugs.append(BugReport(
                id="MAIN_04",
                title="主循环无数据帧时序保护，紧循环可能重复读取同一帧 DRDY",
                severity=Severity.MEDIUM,
                file="Core/Src/main.c",
                lines="173",
                problem=(
                    "即使修改 DRDY 检测逻辑后，仍需确保每次读取之间至少间隔一个 DRDY 周期。\n"
                    "当前主循环无任何延迟或时间戳检查，可能在不同帧之间连续读取。"
                ),
                impact="极端条件下 (高频 MCU 时钟) 可能一帧 DRDY 期间被多次读取。",
                fix=(
                    "记录上次读取时间戳：\n"
                    "  static uint32_t last_read_tick = 0;\n"
                    "  if (HAL_GetTick() - last_read_tick >= 1) {  // 至少间隔 1ms\n"
                    "      // read chips + last_read_tick = HAL_GetTick();\n"
                    "  }"
                ),
            ))

    # ── Bug 9: DRDY Watchdog 具体实现检查 ────────────────────────────

    def _check_drdy_watchdog_implementation(self) -> None:
        f = self.src.main

        # 检查是否仍然包含 HAL_GPIO_ReadPin 作为 OR 条件
        has_or_gpio_read = re.search(
            r"if\s*\(\s*data_ready_flag.*\|\|.*HAL_GPIO_ReadPin",
            f.content
        )

        # 检查 drdy_watchdog 的递增逻辑是否存在
        has_watchdog_increment = "++drdy_watchdog" in f.content or "drdy_watchdog++" in f.content

        # 检查 EXTI 是否正确使能
        has_exti_enable = "HAL_NVIC_EnableIRQ" in f.content and "DRDY" in f.content

        if has_or_gpio_read:
            self.bugs.append(BugReport(
                id="MAIN_05",
                title="DRDY 检测使用 OR 逻辑混合 EXTI 标志和 GPIO 电平轮询",
                severity=Severity.CRITICAL,
                file="Core/Src/main.c",
                lines="173",
                problem=(
                    "条件 'data_ready_flag == 1 || HAL_GPIO_ReadPin(...) == GPIO_PIN_RESET' \n"
                    "以 OR 逻辑同时检查 EXTI 标志和 GPIO 电平。\n"
                    "电平模式下，GPIO_ReadPin 几乎恒为 RESET → 条件恒真。"
                ),
                impact="每轮 50µs 读取同一批数据 → 队列瞬间溢出 → 数据大量丢失。",
                fix="改为纯 EXTI 驱动：if (data_ready_flag == 1) { ... }",
            ))

        if not has_exti_enable:
            self.bugs.append(BugReport(
                id="MAIN_06",
                title="DRDY EXTI 中断未使能或使能代码不可见",
                severity=Severity.HIGH,
                file="Core/Src/main.c",
                lines="156",
                problem="未找到 HAL_NVIC_EnableIRQ(EXTI15_10_IRQn) 调用。DRDY 中断可能未正常工作。",
                impact="data_ready_flag 永不会被 EXTI 设置，系统仅依赖 GPIO 电平轮询。",
                fix="添加 HAL_NVIC_EnableIRQ(EXTI15_10_IRQn); 确保 EXTI 中断使能。",
            ))


# ──────────────────────────────────────────
#  时序分析器
# ──────────────────────────────────────────

class TimingAnalyzer:
    """分析 SPI 读取 vs UART 发送的时序关系"""

    def __init__(self, src: SourceSet):
        self.src = src
        self._parse_config()

    def _parse_config(self):
        """从源码中提取关键配置参数"""
        # CHANNEL_COUNT (ads131m0x.h)
        ch_match = re.search(r"#define\s+CHANNEL_COUNT\s+\((\d+)\)", self.src.ads_h.content)
        self.channel_count = int(ch_match.group(1)) if ch_match else 8

        # OSR (ads131m0x.c adcStartup)
        osr_match = re.search(r"CLOCK_OSR_(\d+)", self.src.ads.content)
        self.osr = int(osr_match.group(1)) if osr_match else 16384

        # 帧长度 (queue.h)
        frame_match = re.search(r"#define\s+transm_data_len\s+(\d+)", self.src.queue_h.content)
        self.frame_len = int(frame_match.group(1)) if frame_match else 29

        # SPI 波特率预分频器 (spi.c)
        prescaler_match = re.search(r"SPI_BAUDRATEPRESCALER_(\d+)", self.src.spi.content)
        self.spi_prescaler = int(prescaler_match.group(1)) if prescaler_match else 2

        # PLL multiplier (main.c SystemClock_Config)
        pll_match = re.search(r"RCC_PLL_MUL(\d+)", self.src.main.content)
        self.pll_mul = int(pll_match.group(1)) if pll_match else 9

        # Queue capacity (main.c)
        cap_match = re.search(r"createQueue\((\d+)\)", self.src.main.content)
        self.queue_capacity = int(cap_match.group(1)) if cap_match else 50

        # 4 chips
        self.num_chips = 4

    def compute(self) -> TimingReport:
        """执行时序计算"""
        # 系统时钟
        sysclk_mhz = 8.0 * self.pll_mul  # HSE 8MHz * PLL_MUL
        spi_clk_mhz = sysclk_mhz / self.spi_prescaler

        # UART 参数
        uart_baud = 921600
        uart_bits_per_byte = 10  # 1 start + 8 data + 1 stop

        # SPI 读取时序
        # 每芯片 27 bytes × 8 bits => 216 bits
        # HAL_SPI_Receive 有 ~1µs 函数调用开销 / 芯片
        bits_per_chip = 27 * 8
        spi_overhead_us = 2.0  # CS 切换 + 函数调用开销
        spi_time_per_chip_us = (bits_per_chip / spi_clk_mhz) + spi_overhead_us

        # 4 芯片总时间
        spi_read_4chips_us = self.num_chips * spi_time_per_chip_us

        # UART 时序
        uart_frame_us = (self.frame_len * uart_bits_per_byte / uart_baud) * 1e6
        uart_4frames_us = self.num_chips * uart_frame_us

        # 速率比
        read_send_ratio = uart_4frames_us / spi_read_4chips_us

        # DRDY 时序
        # fCLKIN = 8.192MHz (通常外部晶振分频)
        # fMOD = fCLKIN / 2 = 4.096MHz
        # ODR = fMOD / OSR = 4.096MHz / 16384 = 250Hz
        fmod_mhz = 4.096
        odr_hz = fmod_mhz * 1e6 / self.osr
        drdy_period_us = 1e6 / odr_hz

        # 电平模式: DRDY 在一个 ODR 周期内低电平时间
        # 低电平持续时间 ≈ ODR 周期 - (少量高电平时间 ~122ns)
        drdy_low_us = drdy_period_us - 0.122

        # 队列填满时间 (在紧循环模式下)
        # 每次紧循环: SPI 读 4 芯片 ~50µs → enqueue 4 帧
        # UART DMA 同时发送: 1 frame / 315µs
        # 净入队速率 = 4 frames / 50µs - 4 frames / 1260µs
        #             ≈ 80000 - 3174 ≈ 76825 frames/sec
        # 队列填满时间 = 50 / 76825 * 1000 ≈ 0.65 ms
        enqueue_rate = 4.0 / (spi_read_4chips_us * 1e-6)  # frames/s
        dequeue_rate = 4.0 / (uart_4frames_us * 1e-6)      # frames/s
        net_fill_rate = enqueue_rate - dequeue_rate
        queue_fill_time_ms = (self.queue_capacity / max(net_fill_rate, 0.001)) * 1e3

        return TimingReport(
            spi_read_4chips_us=spi_read_4chips_us,
            uart_frame_us=uart_frame_us,
            uart_4frames_us=uart_4frames_us,
            read_send_ratio=read_send_ratio,
            drdy_period_us=drdy_period_us,
            drdy_low_duration_us=drdy_low_us,
            queue_capacity=self.queue_capacity,
            queue_fill_time_ms=queue_fill_time_ms,
        )


# ──────────────────────────────────────────
#  报告生成器
# ──────────────────────────────────────────

class ReportGenerator:
    """生成诊断报告 (控制台 + 文件)"""

    def __init__(self, bugs: List[BugReport], timing: TimingReport, src: SourceSet):
        self.bugs = bugs
        self.timing = timing
        self.src = src

    def generate_console(self) -> str:
        sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        bugs_sorted = sorted(
            self.bugs,
            key=lambda b: sev_order.index(b.severity.value) if b.severity.value in sev_order else 99,
        )

        critical_count = sum(1 for b in self.bugs if b.severity == Severity.CRITICAL)
        high_count = sum(1 for b in self.bugs if b.severity == Severity.HIGH)
        medium_count = sum(1 for b in self.bugs if b.severity == Severity.MEDIUM)
        low_count = sum(1 for b in self.bugs if b.severity == Severity.LOW)

        header = f"""
{'═' * 78}
                    STM32 Firmware 静态诊断报告
            PiezoTac 32CH — ADS131M0x ×4 + STM32F1
{'═' * 78}

  检测到 {len(self.bugs)} 个 Bug:
    🔴 CRITICAL : {critical_count}
    🟠 HIGH     : {high_count}
    🟡 MEDIUM   : {medium_count}
    🔵 LOW      : {low_count}

{self.timing}

"""

        bug_section = "".join(str(b) for b in bugs_sorted)

        # 汇总修复优先级
        fix_order = (
            f"\n{'═' * 78}\n"
            f"  修复优先级\n"
            f"{'═' * 78}\n"
        )

        priority_fixes = [
            ("最优先 (P0) — 修复后系统应可正常工作",
             [
                 "BUG-MAIN_01: 修改 DRDY 检测为纯 EXTI 驱动 + watchdog 备份",
                 "BUG-QUEUE_01: enqueue() 满时覆盖旧数据 + 添加 overrun 计数器",
                 "BUG-HAL_01: 修复 setCS() 的 GPIO_Pin 截断问题",
             ]),
            ("高优先级 (P1) — 提升数据可靠性和可诊断性",
             [
                 "BUG-ADS_01: adcStartup() 末尾添加 toggleSYNC()",
                 "BUG-MAIN_02: 实现 drdy_watchdog 在 while(1) 中的递增/检查逻辑",
                 "BUG-QUEUE_02: dequeue() 空队列时返回零初始化值",
             ]),
            ("建议 (P2) — 优化/增强",
             [
                 "BUG-MAIN_04: 添加帧间时间戳保护",
                 "BUG-MAIN_03: 考虑 SPI DMA 方式 (性能提升有限，当前非瓶颈)",
             ]),
        ]

        for title, fixes in priority_fixes:
            fix_order += f"\n  ▶ {title}\n"
            for f_text in fixes:
                fix_order += f"      • {f_text}\n"

        return header + bug_section + fix_order

    def write_report(self, output_path: str):
        """将报告写入文件"""
        content = self.generate_console()
        # 移除 Unicode 字符用于纯文本
        content_plain = content.replace("🔴", "[CRIT]").replace("🟠", "[HIGH]")\
                              .replace("🟡", "[MED]").replace("🔵", "[LOW]")\
                              .replace("⏱", "[TIMING]").replace("═", "=")\
                              .replace("─", "-").replace("▶", ">").replace("•", "*")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content_plain)
        print(f"\n  报告已保存至: {output_path}")


# ──────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────

def guess_project_root() -> str:
    """尝试自动定位项目根目录"""
    cwd = os.getcwd()
    # 检查当前目录
    if os.path.exists(os.path.join(cwd, "Core", "Src", "main.c")):
        return cwd
    # 向上一级
    parent = os.path.dirname(cwd)
    if os.path.exists(os.path.join(parent, "Core", "Src", "main.c")):
        return parent
    return cwd


def parse_args():
    parser = argparse.ArgumentParser(
        description="STM32 Firmware Diagnostic Tool — PiezoTac 32CH"
    )
    parser.add_argument(
        "--project-dir", "-p",
        default=None,
        help="项目根目录 (默认自动检测)"
    )
    parser.add_argument(
        "--report", "-r",
        default=None,
        help="生成报告文件路径"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="输出详细调试信息"
    )
    parser.add_argument(
        "--timing-only", "-t",
        action="store_true",
        help="仅显示时序分析"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    project_dir = args.project_dir or guess_project_root()
    core_dir = os.path.join(project_dir, "Core")

    if not os.path.exists(core_dir):
        print(f"错误: 找不到 Core 目录: {core_dir}")
        print(f"请使用 --project-dir 指定正确的项目根目录")
        sys.exit(1)

    print(f"项目目录: {project_dir}")

    # 加载源文件
    src = SourceSet(project_dir)

    # Bug 检测
    detector = BugDetector(src)
    bugs = detector.detect_all()

    # 时序分析
    timing_analyzer = TimingAnalyzer(src)
    timing = timing_analyzer.compute()

    if args.timing_only:
        print(timing)
        return

    # 生成报告
    report_gen = ReportGenerator(bugs, timing, src)
    report = report_gen.generate_console()
    print(report)

    if args.report:
        report_gen.write_report(args.report)

    # 返回非零退出码表示有 CRITICAL bug
    critical_count = sum(1 for b in bugs if b.severity == Severity.CRITICAL)
    if critical_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
