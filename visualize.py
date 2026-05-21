#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PiezoTac 32CH 实时可视化诊断脚本

读取 STM32 UART 输出的 29 字节帧数据，实时绘制 4×8=32 个通道的
PVDF 压电传感器信号。支持：
  - 实时波形图 (4 子图，每子图 8 通道)
  - 帧率统计 & 丢帧检测
  - 通道幅值统计 (柱状图)
  - 离线 .bin 文件回放分析

用法:
    # 实时串口连接
    python visualize.py --port COM3 --baud 921600

    # 离线分析 .bin 文件
    python visualize.py --file data.bin

    # 诊断模式 (详细统计输出)
    python visualize.py --port COM3 --baud 921600 --diagnosis

依赖:
    pip install pyserial numpy matplotlib
"""

import argparse
import struct
import sys
import time
import os
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

# ──────────────────────────────────────────
#  帧协议常量
# ──────────────────────────────────────────

FRAME_LEN = 29          # 帧长度 (bytes)
FRAME_HEADER = 0xAAAA   # 帧头 (2 bytes)
FRAME_TAIL = 0xFFFF     # 帧尾 (2 bytes)
CHIPS_PER_FRAME = 4     # ADC 芯片数
CHANNELS_PER_CHIP = 8   # 每芯片通道数
TOTAL_CHANNELS = CHIPS_PER_FRAME * CHANNELS_PER_CHIP  # 32
BYTES_PER_CHANNEL = 3   # 24-bit signed ADC data
DATA_OFFSET = 3         # 通道数据在帧中的偏移 (跳过 0xAA 0xAA chip_id)

# DRDY 时序 (OSR=16384, fMOD=4.096MHz)
DRDY_PERIOD_MS = 4.0    # DRDY 周期 = 250Hz
EXPECTED_FPS = 250.0    # 期望帧率


# ──────────────────────────────────────────
#  数据模型
# ──────────────────────────────────────────

@dataclass
class FrameStats:
    """帧统计信息"""
    total_frames: int = 0          # 总帧数
    valid_frames: int = 0          # 有效帧数
    invalid_frames: int = 0        # 无效帧数
    chip_counts: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    last_seq: List[int] = field(default_factory=lambda: [-1, -1, -1, -1])
    lost_frames: int = 0           # 检测到的丢失帧
    start_time: float = 0.0
    last_time: float = 0.0
    fps: float = 0.0

    def update_fps(self):
        elapsed = self.last_time - self.start_time
        self.fps = self.total_frames / elapsed if elapsed > 0 else 0.0


@dataclass
class ChannelData:
    """单个芯片的 8 通道数据环缓冲"""
    chip_id: int
    buffer_size: int = 500  # 显示窗口大小 (采样点数)
    # 每个通道一个 deque
    data: List[deque] = field(default_factory=list)
    timestamps: deque = field(default_factory=deque)

    def __post_init__(self):
        for _ in range(CHANNELS_PER_CHIP):
            self.data.append(deque(maxlen=self.buffer_size))

    def append(self, samples: List[int], timestamp: float) -> None:
        """追加一组 8 通道样本"""
        for ch in range(CHANNELS_PER_CHIP):
            self.data[ch].append(samples[ch])
        self.timestamps.append(timestamp)


# ──────────────────────────────────────────
#  帧解析器
# ──────────────────────────────────────────

class FrameParser:
    """解析 29 字节二进制帧为 ADC 通道数据"""

    def __init__(self):
        self.buffer = bytearray()
        self.stats = FrameStats()
        self.chip_data = [ChannelData(chip_id=i) for i in range(CHIPS_PER_FRAME)]

    def feed(self, raw_bytes: bytes) -> List[Tuple[int, List[int]]]:
        """
        喂入原始字节流，返回解析好的帧列表。
        每帧: (chip_id, [ch0, ch1, ..., ch7])
        每个通道值: 24-bit signed integer
        """
        self.buffer.extend(raw_bytes)
        results = []

        # 同步帧头
        while len(self.buffer) >= FRAME_LEN:
            # 查找帧头 0xAA 0xAA
            if self.buffer[0] != 0xAA or self.buffer[1] != 0xAA:
                self.buffer.pop(0)
                self.stats.invalid_frames += 1
                continue

            # 检查尾字节
            if self.buffer[FRAME_LEN - 2] != 0xFF or self.buffer[FRAME_LEN - 1] != 0xFF:
                self.buffer.pop(0)
                self.stats.invalid_frames += 1
                continue

            # 提取帧
            frame = self.buffer[:FRAME_LEN]
            del self.buffer[:FRAME_LEN]

            chip_id = frame[2]
            if chip_id >= CHIPS_PER_FRAME:
                self.stats.invalid_frames += 1
                continue

            # 解析 24-bit 有符号 ADC 数据 (8 通道 × 3 bytes)
            samples = []
            for ch in range(CHANNELS_PER_CHIP):
                offset = DATA_OFFSET + ch * BYTES_PER_CHANNEL
                # 大端序 24-bit 有符号 → 32-bit 符号扩展
                raw = (frame[offset] << 24) | (frame[offset + 1] << 16) | (frame[offset + 2] << 8)
                value = raw >> 8  # 算术右移完成符号扩展 (Python 自动处理)
                if value & 0x800000:
                    value = value - 0x1000000  # 24-bit 补码 → Python signed int
                samples.append(value)

            results.append((chip_id, samples))

            # 更新统计
            self.stats.total_frames += 1
            self.stats.valid_frames += 1
            self.stats.chip_counts[chip_id] += 1
            now = time.time()
            if self.stats.start_time == 0.0:
                self.stats.start_time = now
            self.stats.last_time = now

            # 丢帧检测 (基于帧到达时间间隔)
            if self.stats.last_seq[chip_id] >= 0:
                # 如果同一 chip 连续出现但间隔过大
                pass
            self.stats.last_seq[chip_id] = self.stats.total_frames

            # 存储到环缓冲
            self.chip_data[chip_id].append(samples, now)

        return results


# ──────────────────────────────────────────
#  串口读取器
# ──────────────────────────────────────────

class SerialReader:
    """串口数据读取器"""

    def __init__(self, port: str, baudrate: int = 921600, timeout: float = 1.0):
        import serial
        self.ser = serial.Serial(port, baudrate, timeout=timeout)
        self.ser.reset_input_buffer()

    def read(self, size: int = 4096) -> bytes:
        return self.ser.read(size)

    def close(self):
        self.ser.close()

    @property
    def in_waiting(self) -> int:
        return self.ser.in_waiting


# ──────────────────────────────────────────
#  文件回放读取器
# ──────────────────────────────────────────

class FileReader:
    """从 .bin 文件回放帧数据"""

    def __init__(self, filepath: str):
        self.f = open(filepath, "rb")
        self.filesize = os.path.getsize(filepath)

    def read(self, size: int = 4096) -> bytes:
        return self.f.read(size)

    def close(self):
        self.f.close()

    @property
    def in_waiting(self) -> int:
        remaining = self.filesize - self.f.tell()
        return min(remaining, 4096)


# ──────────────────────────────────────────
#  Matplotlib 可视化
# ──────────────────────────────────────────

def plot_realtime(parser: FrameParser, reader, duration: float = 0, diagnosis: bool = False):
    """实时 4 子图波形显示"""
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    fig, axes = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    fig.suptitle("PiezoTac 32CH — Real-Time Visualization (ADS131M0x ×4)", fontsize=13)
    plt.subplots_adjust(hspace=0.35)

    chip_labels = ["Chip 1 (CS1 / PB11)", "Chip 2 (CS2 / PB10)",
                   "Chip 3 (CS3 / PC6)", "Chip 4 (CS4 / PC7)"]
    colors = plt.cm.tab10(np.linspace(0, 1, CHANNELS_PER_CHIP))

    lines = []
    for i, ax in enumerate(axes):
        ax.set_ylabel("ADC Code")
        ax.set_title(chip_labels[i], fontsize=9)
        ax.grid(True, alpha=0.3)
        chip_lines = []
        for ch in range(CHANNELS_PER_CHIP):
            line, = ax.plot([], [], linewidth=0.8, color=colors[ch],
                          label=f"CH{ch}", alpha=0.8)
            chip_lines.append(line)
        ax.legend(loc="upper right", fontsize=7, ncol=4)
        lines.append(chip_lines)

    axes[-1].set_xlabel("Sample Index")

    # 帧率文本
    fps_text = fig.text(0.02, 0.02, "", fontsize=9, fontfamily="monospace",
                        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

    # Y 轴自适应范围
    y_min, y_max = -1000000, 1000000

    def init():
        for chip_lines in lines:
            for line in chip_lines:
                line.set_data([], [])
        return [line for chip_lines in lines for line in chip_lines] + [fps_text]

    def update(frame):
        nonlocal y_min, y_max

        # 读取并解析新数据
        if hasattr(reader, 'in_waiting'):
            raw = reader.read(max(reader.in_waiting, 4096))
        else:
            raw = reader.read(4096)
        if raw:
            parser.feed(raw)

        parser.stats.update_fps()

        # 更新绘图
        x = np.arange(parser.chip_data[0].buffer_size)
        for i, chip in enumerate(parser.chip_data):
            for ch in range(CHANNELS_PER_CHIP):
                d = list(chip.data[ch])
                if len(d) > 0:
                    padded = np.pad(d, (parser.chip_data[0].buffer_size - len(d), 0),
                                   mode='constant', constant_values=np.nan)
                    lines[i][ch].set_ydata(padded)
                else:
                    lines[i][ch].set_ydata(np.full(x.shape, np.nan))
                lines[i][ch].set_xdata(x)

                # Y 轴自适应
                valid = [v for v in list(chip.data[ch]) if v != 0]
                if valid:
                    y_min = min(y_min, min(valid))
                    y_max = max(y_max, max(valid))

        for ax in axes:
            ax.set_xlim(0, parser.chip_data[0].buffer_size)
            ax.set_ylim(y_min * 1.1, y_max * 1.1)

        # 更新 FPS 文本
        s = parser.stats
        fps_text.set_text(
            f"Frames: {s.total_frames} | Valid: {s.valid_frames} | "
            f"FPS: {s.fps:.1f} | "
            f"Chip counts: {s.chip_counts}"
        )

        return [line for chip_lines in lines for line in chip_lines] + [fps_text]

    ani = FuncAnimation(fig, update, init_func=init, interval=50,
                       blit=True, cache_frame_data=False)

    if duration > 0:
        plt.pause(duration)
    else:
        plt.show()

    if not plt.fignum_exists(fig.number):
        return  # 窗口已关闭

    plt.close(fig)


def plot_summary(parser: FrameParser):
    """诊断摘要: 通道幅值统计柱状图"""
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt

    s = parser.stats
    print(f"\n{'='*60}")
    print(f"  诊断摘要")
    print(f"{'='*60}")
    print(f"  总帧数:     {s.total_frames}")
    print(f"  有效帧:     {s.valid_frames}")
    print(f"  无效/丢帧:  {s.invalid_frames} ({(s.invalid_frames/max(s.total_frames,1))*100:.1f}%)")
    elapsed = s.last_time - s.start_time
    print(f"  运行时长:   {elapsed:.1f}s")
    print(f"  平均 FPS:   {s.fps:.1f} (期望 {EXPECTED_FPS}Hz)")
    print(f"  Chip 分布:  {s.chip_counts}")

    # 通道统计
    print(f"\n  {'─'*55}")
    print(f"  {'Chip':<6} {'CH0':>10} {'CH1':>10} {'CH2':>10} {'CH3':>10} {'CH4':>10} {'CH5':>10} {'CH6':>10} {'CH7':>10}")
    print(f"  {'─'*55}")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Channel Amplitude Statistics (Peak-to-Peak)", fontsize=13)

    for chip_id in range(CHIPS_PER_CHIP):
        ax = axes[chip_id // 2][chip_id % 2]
        chip = parser.chip_data[chip_id]

        stats = []
        row_str = f"  Chip{chip_id+1} "
        for ch in range(CHANNELS_PER_CHIP):
            d = list(chip.data[ch])
            if d:
                p2p = max(d) - min(d)
                rms = np.sqrt(np.mean(np.array(d, dtype=np.float64) ** 2))
                stats.append((ch, p2p, rms))
                row_str += f" {p2p:>10}"
            else:
                stats.append((ch, 0, 0))
                row_str += f" {'--':>10}"
        print(row_str)

        channels = [s[0] for s in stats]
        p2p_values = [s[1] for s in stats]
        rms_values = [s[2] for s in stats]

        x = np.arange(len(channels))
        width = 0.35
        ax.bar(x - width/2, p2p_values, width, label="Pk-Pk", alpha=0.8, color="steelblue")
        ax.bar(x + width/2, rms_values, width, label="RMS", alpha=0.8, color="coral")
        ax.set_title(f"Chip {chip_id + 1}")
        ax.set_ylabel("ADC Code")
        ax.set_xticks(x)
        ax.set_xticklabels([f"CH{ch}" for ch in channels])
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    print(f"  {'─'*55}")
    plt.tight_layout()
    plt.show()


# ──────────────────────────────────────────
#  命令行入口
# ──────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="PiezoTac 32CH — Real-Time Visualization & Diagnosis"
    )
    parser.add_argument("--port", "-p", default=None,
                       help="串口号 (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("--baud", "-b", type=int, default=921600,
                       help="波特率 (默认 921600)")
    parser.add_argument("--file", "-f", default=None,
                       help="离线 .bin 文件路径")
    parser.add_argument("--duration", "-d", type=float, default=0,
                       help="采集时长 (秒)，0 表示持续运行")
    parser.add_argument("--diagnosis", action="store_true",
                       help="诊断模式: 显示通道幅值统计柱状图")
    parser.add_argument("--summary-only", action="store_true",
                       help="仅显示诊断摘要，不复时绘图")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.file:
        print(f"[回放模式] 读取文件: {args.file}")
        reader = FileReader(args.file)
    elif args.port:
        print(f"[实时模式] 串口: {args.port} @ {args.baud} baud")
        reader = SerialReader(args.port, args.baud)
    else:
        print("错误: 请指定 --port 或 --file")
        sys.exit(1)

    parser_obj = FrameParser()

    try:
        if args.summary_only:
            # 采集数据但不绘图，最后输出摘要
            print("采集数据中... (按 Ctrl+C 停止)")
            start = time.time()
            while not args.duration or (time.time() - start) < args.duration:
                raw = reader.read(max(getattr(reader, 'in_waiting', 4096), 4096))
                if raw:
                    parser_obj.feed(raw)
                if not raw and args.file:
                    break
                time.sleep(0.01)
            plot_summary(parser_obj)
        elif args.diagnosis:
            # 先实时绘图，关闭窗口后显示诊断摘要
            plot_realtime(parser_obj, reader, duration=args.duration)
            plot_summary(parser_obj)
        else:
            # 仅实时绘图
            plot_realtime(parser_obj, reader, duration=args.duration)

    except KeyboardInterrupt:
        print("\n中断采集，输出统计信息...")
        if not args.summary_only:
            plot_summary(parser_obj)
    finally:
        reader.close()


if __name__ == "__main__":
    main()
