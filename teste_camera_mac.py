"""Diagnostico de camera no macOS para o pcb_inspector_mac.py.

Uso (no Terminal do macOS, nao no terminal da IDE):  python3 teste_camera_mac.py
Testa os indices 0 a 4 pelo AVFoundation, mostra o tamanho do quadro recebido
e salva uma foto de cada camera que abrir (cam_mac_<n>.png), para voce ver qual
e a camera de inspecao. Copie a saida inteira e envie.
"""
import os
import platform
import sys
import time

import cv2

print("Python", sys.version.split()[0], "|", platform.platform(), "|", platform.machine())
print("OpenCV", cv2.__version__)
print("Executado por:", os.environ.get("TERM_PROGRAM", "?"), "(o macOS da a permissao de camera a este app)")
vio = [l.strip() for l in cv2.getBuildInformation().splitlines() if "AVFoundation" in l]
print("Build com AVFoundation:", vio or "NAO ENCONTRADO")
print()

HERE = os.path.dirname(os.path.abspath(__file__))
for attempt in (1, 2):
    found = 0
    for idx in range(5):
        cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            print(f"indice {idx}: nao abriu")
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        frame = None
        for _ in range(20):
            ok, f = cap.read()
            if ok:
                frame = f
        cap.release()
        if frame is None:
            print(f"indice {idx}: abriu, mas nao entregou quadros (permissao?)")
            continue
        found += 1
        path = os.path.join(HERE, f"cam_mac_{idx}.png")
        cv2.imwrite(path, frame)
        print(f"indice {idx}: OK {frame.shape[1]}x{frame.shape[0]}, brilho medio {frame.mean():.0f} -> {os.path.basename(path)}")
    if found or attempt == 2:
        break
    print("\nNenhuma camera abriu. Se o macOS mostrou o pedido de permissao, aceite;"
          " tentando de novo em 5 s...\n")
    time.sleep(5)

if not found:
    print("\nNenhuma camera abriu. Verifique em Ajustes do Sistema > Privacidade e Seguranca > Camera"
          " se o app acima (Terminal/VS Code/...) esta ativado; depois feche-o com Cmd+Q e reabra.")
