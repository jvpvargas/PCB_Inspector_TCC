PCB INSPECTOR
Inspeção óptica automatizada (AOI) de placas de circuito impresso por comparação com uma placa de referência (Golden Board)

Trabalho de Conclusão de Curso – UTFPR
Autor: <seu nome completo>


1. DESCRIÇÃO

O PCB Inspector compara a imagem de uma placa capturada por uma câmera USB com a imagem de uma placa de referência sem defeitos (Golden Board) e indica, para cada região de interesse (ROI) cadastrada, se ela está OK ou NOK. Os defeitos considerados são componente ausente e furo de terminal fechado com solda.

Funcionamento resumido:
  - Alinhamento da captura com a golden por pontos característicos (ORB + RANSAC/homografia).
  - Verificação de posicionamento: capturas sem placa ("Placa Ausente") ou com a placa fora do quadro ("Placa Fora de Posição") são descartadas e não entram no log.
  - Avaliação de cada ROI conforme o tipo de inspeção:
      component - diferença de cor no espaço Lab (fração de pixels com ΔE > 36; NOK acima de 0,30);
      hole      - furo de terminal: distância de cor a/b dentro do furo (NOK acima de 10);
      metal     - partes metálicas reflexivas (ex.: conector USB): recuperação de bordas (NOK abaixo de 0,80).
  - Resultado na tela (caixas verdes/vermelhas e LEDs virtuais OK/NOK) e registro automático em CSV, com as imagens bruta e anotada de cada placa.

Placas usadas no desenvolvimento e na validação: módulo carregador HW-373 (TP4056, USB-C) e, como teste de generalização, ESP32 DevKit.


2. CONTEÚDO DO REPOSITÓRIO

  pcb_inspector.py          Programa principal - versão Windows
  pcb_inspector_mac.py      Programa principal - versão macOS (mesmo algoritmo e mesmos limiares)
  requirements.txt          Bibliotecas Python necessárias
  teste_camera_mac.py       Diagnóstico de câmera no macOS (lista as câmeras e salva uma foto de cada)
  teste_1080_vs_4k.py       Ensaio de captura em 1920x1080 e 3840x2160
  analise_1080_vs_4k.py     Análise do ensaio 1080p x 4K
  Golden Boards Images/     Imagens de referência (Testes.png = HW-373; ESP32.png = ESP32 DevKit)
  rois/                     Regiões de interesse de cada golden (arquivos .json com o mesmo nome da imagem)

Pastas criadas durante o uso (não versionadas):
  logs/<golden>_<data>_<hora>/   CSV do ensaio, imagem bruta (_raw.png) e anotada de cada placa
  logs/.../descartadas/          capturas descartadas pela verificação de posicionamento


3. REQUISITOS

  - Python 3.10 ou superior (desenvolvido com Python 3.14)
  - Bibliotecas: opencv-python, numpy, Pillow (versões em requirements.txt)
  - Tkinter (já incluído no Python do python.org; no Homebrew: brew install python-tk)
  - Câmera USB com resolução 1920x1080 (usada no TCC em formato MJPG, ~27 fps)
  - Iluminação constante (anel de LED) e um suporte (bandeja) que mantenha a placa no campo da câmera


4. INSTALAÇÃO

  1. Baixe o repositório: botão "Code" > "Download ZIP" no GitHub (ou: git clone <endereço do repositório>) e extraia a pasta.
  2. No terminal, entre na pasta e instale as bibliotecas:
     pip install -r requirements.txt        (no macOS: pip3)


5. EXECUÇÃO

Windows:
  python pcb_inspector.py

macOS:
  python3 pcb_inspector_mac.py

  Se abrir a câmera errada (FaceTime, iPhone), informe o índice da câmera USB:
  PCB_CAMERA_INDEX=1 python3 pcb_inspector_mac.py

  Para descobrir o índice: python3 teste_camera_mac.py


6. USO

  1. "Select Golden Board": capture a placa de referência pela câmera, abra uma imagem ou carregue uma configuração salva (golden + ROIs).
  2. "Setup ROIs": desenhe as regiões sobre a golden, dê um nome a cada uma e escolha o tipo de inspeção (component, hole ou metal). Use Zoom para regiões pequenas; as setas movem a região selecionada e Ctrl+setas (Cmd+setas no Mac) a redimensionam. As ROIs são salvas em rois/<nome da golden>.json. Use nomes únicos para as regiões.
  3. A câmera ao vivo inicia automaticamente. Coloque a placa na bandeja e pressione Espaço (ou "Capture").
  4. O resultado fica na tela por 2 s e a câmera volta para a próxima placa. O histórico mostra cada inspeção; um duplo clique abre a imagem anotada.
  5. Os resultados ficam em logs/<golden>_<data>_<hora>/ (CSV com data, hora, contagem, status, número de erros e regiões NOK).


7. CONFIGURAÇÃO

Os parâmetros ficam em constantes no início de pcb_inspector.py / pcb_inspector_mac.py. Valores usados no TCC:

  CAMERA_INDEX                 1 no Windows; 0 no macOS (ou variável PCB_CAMERA_INDEX)
  CAMERA_WIDTH x CAMERA_HEIGHT 1920 x 1080
  RESULT_DISPLAY_MS            2000 (tempo de exibição do resultado, em ms)
  ROI_DIFF_RATIO_THRESHOLD     0,30 (limiar das ROIs do tipo component)
  HOLE_CHROMA_THRESHOLD        10   (limiar das ROIs do tipo hole)
  METAL_EDGE_RECALL_MIN        0,80 (limiar das ROIs do tipo metal)
  PLACEMENT_MIN_INLIERS        100  (abaixo disso: "Placa Ausente")
  PLACEMENT_SCALE_RANGE        0,8 a 1,25 (fora disso ou ROI fora do quadro: "Placa Fora de Posição")

Os limiares foram definidos e congelados antes da validação. Alterá-los muda o comportamento validado.


8. DIFERENÇAS ENTRE AS VERSÕES WINDOWS E macOS

O núcleo de inspeção é idêntico. Mudam apenas os recursos que dependem do sistema operacional:

  Item                          Windows                       macOS
  Acesso à câmera               Media Foundation (CAP_MSMF)   AVFoundation (CAP_AVFOUNDATION)
  Índice da câmera              fixo (1)                      0 ou variável PCB_CAMERA_INDEX
  Abrir imagem do histórico     os.startfile                  comando "open"
  Fontes                        Segoe UI / Arial              Helvetica Neue / Arial Bold do macOS
  Atalhos na tela de ROIs       Ctrl                          Ctrl ou Cmd


9. OBSERVAÇÕES

  - macOS: na primeira execução o sistema pede permissão de câmera para o aplicativo que executa o Python (Terminal, VS Code etc.). Se nenhuma câmera abrir, ative-o em Ajustes do Sistema > Privacidade e Segurança > Câmera, feche-o com Cmd+Q e abra novamente.
  - Exposição da câmera: no TCC, exposição e ganho foram fixados no driver da câmera (exposição -6, ganho 32). O programa não altera esses valores. Em outro computador, fixe-os antes de inspecionar (no macOS, por exemplo, com o utilitário CameraController), pois a exposição automática altera o brilho entre capturas.
  - Os arquivos de ROIs guardam o caminho da golden. Se o projeto for copiado para outra pasta ou outro computador, o programa procura a imagem pelo nome em "Golden Boards Images/".
  - Windows: a variável OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0 é definida pelo próprio programa e reduz a abertura da câmera de ~17 s para ~0,8 s.


10. RESULTADOS (resumo do TCC)

  - Validação: 40 placas HW-373 x 5 rodadas = 200 capturas; acurácia por ROI de 99,93%; 199/200 vereditos de placa corretos.
  - Tempo: ciclo mediano de 9 s por placa (operador + captura + processamento + exibição); processamento de ~0,46 s por captura.
  - Portabilidade: executado no macOS com resultados corretos em 11/11 placas.
  - Generalização: ESP32 DevKit inspecionada com o mesmo código e os mesmos limiares, apenas com nova golden e novas ROIs.
