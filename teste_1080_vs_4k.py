"""Teste 1080p vs 4K: para cada placa colocada na bandeja, captura um quadro em
1920x1080 e outro em 3840x2160, em sequencia, sem mexer na placa.

Uso:  python teste_1080_vs_4k.py
  - Feche o PCB Inspector antes (a camera so pode ser usada por um programa).
  - Para cada placa: coloque-a na bandeja, digite o numero dela e tecle Enter.
  - Comece pela placa 1 (ela vira a golden nas duas resolucoes).
  - Para repetir uma placa, use um sufixo (ex.: 1b, 15b).
  - Digite "fim" para encerrar.
As imagens ficam em "00. teste 1080 vs 4k/" ao lado deste script.
"""
import datetime
import os
import time

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
import cv2  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "00. teste 1080 vs 4k")
MODES = {"1080": (1920, 1080), "4k": (3840, 2160)}
SUGGESTED = "1, 15, 37, 16, 2, 25 (e, se quiser, 1b, 15b, 37b recolocadas)"


def grab(w, h, warmup=15):
    """Abre a camera ja no modo pedido, captura um quadro e fecha.
    (Trocar a resolucao com a camera aberta corrompe os quadros no MSMF.)"""
    cap = cv2.VideoCapture(1, cv2.CAP_MSMF)
    if not cap.isOpened():
        raise RuntimeError("nao abriu a camera (feche o PCB Inspector)")
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        frame = None
        for _ in range(warmup):  # deixa a camera assentar (exposicao, foco)
            try:
                ok, f = cap.read()
            except cv2.error:
                continue
            if ok:
                frame = f
    finally:
        cap.release()
    if frame is None or (frame.shape[1], frame.shape[0]) != (w, h):
        got = "nada" if frame is None else f"{frame.shape[1]}x{frame.shape[0]}"
        raise RuntimeError(f"pedi {w}x{h} e recebi {got}")
    return frame


def main():
    os.makedirs(OUT, exist_ok=True)
    print(__doc__)
    print("Sugestao de placas:", SUGGESTED, "\n")
    log = open(os.path.join(OUT, "capturas.csv"), "a", encoding="utf-8")
    try:
        while True:
            label = input("Placa na bandeja (numero, ex. 15) ou 'fim': ").strip()
            if label.lower() in ("fim", "sair", "q", ""):
                break
            t0 = time.time()
            try:
                frames = {k: grab(*wh) for k, wh in MODES.items()}
            except RuntimeError as exc:
                print("  ERRO:", exc)
                continue
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for k, fr in frames.items():
                cv2.imwrite(os.path.join(OUT, f"placa{label}_{k}.png"), fr)
            log.write(f"{stamp};{label};{time.time() - t0:.1f}\n")
            log.flush()
            print(f"  ok: placa{label}_1080.png e placa{label}_4k.png ({time.time() - t0:.1f} s). Pode trocar a placa.")
    finally:
        log.close()
    print("\nImagens em:", OUT)


if __name__ == "__main__":
    main()
    try:
        input("Pressione Enter para fechar...")
    except EOFError:
        pass
