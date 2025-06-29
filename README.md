<div align="center">
  
#  <a href="https://github.com/SlenderSolo/zapret-manager">zapret-manager</a> <img src="https://cdn-icons-png.flaticon.com/128/5968/5968756.png" height=28 /> <img src="https://cdn-icons-png.flaticon.com/128/1384/1384060.png" height=28 /> <img src="https://cdn-icons-png.flaticon.com/512/5968/5968819.png" height=28 />

Альтернативы https://github.com/bol-van/zapret-win-bundle https://github.com/Flowseal/zapret-discord-youtube
</div>

> [!CAUTION]
>
> ### АНТИВИРУСЫ
> WinDivert может вызвать реакцию антивируса.
> WinDivert - это инструмент для перехвата и фильтрации трафика, необходимый для работы zapret.
> Замена iptables и NFQUEUE в Linux, которых нет под Windows.
> Он может использоваться как хорошими, так и плохими программами, но сам по себе не является вирусом.
> Драйвер WinDivert64.sys подписан для возможности загрузки в 64-битное ядро Windows.
> Но антивирусы склонны относить подобное к классам повышенного риска или хакерским инструментам.
> В случае проблем используйте исключения или выключайте антивирус совсем.
>
> **Выдержка из [`readme.md`](https://github.com/bol-van/zapret-win-bundle/blob/master/readme.md#%D0%B0%D0%BD%D1%82%D0%B8%D0%B2%D0%B8%D1%80%D1%83%D1%81%D1%8B) репозитория [bol-van/zapret-win-bundle](https://github.com/bol-van/zapret-win-bundle)*

> [!IMPORTANT]
> Все бинарные файлы в папке [`bin`](./bin) взяты из [zapret-win-bundle/zapret-winws](https://github.com/bol-van/zapret-win-bundle/tree/master/zapret-winws) и [curl](https://curl.se/download.html). Вы можете это проверить с помощью хэшей/контрольных сумм. Проверяйте, что запускаете, используя сборки из интернета!

## Установка

1. Установите [Python](https://python.org/downloads/)

2. Откройте командную строку (Win+R, `cmd`) и введите:
```cmd
pip install colorama
```

3. Загрузите архив (zip/7z) со [страницы последнего релиза](https://github.com/SlenderSolo/zapret-manager/releases/latest)

4. Распакуйте содержимое архива по пути, который не содержит кириллицу/спец. символы

5. Запустите любой пресет стратегий 

## ℹКраткие описания файлов

- [**`preset....cmd`**](./preset_fakeds_m.cmd) - запуск пресета стратегией для обхода блокировок  
  **Работоспособность той или иной стратегии зависит от многих факторов. Пробуйте разные пресеты стратегии, пока не найдёте рабочее для вас решение**

- [**`main.py`**](./main.py) - вспомогательные скрипты:
  - <ins>**`Create/Update Service`** - Добавление или обновление любого пресета стратегий в автозапуск (services.msc)</ins>
  - **`Delete Service`** - удаление пресета стратегий из служб (автозапуска)
  - **`Check Service Status`** - проверка статуса службы (автозапуска)
  - **`Auto-adjust Preset`** - на основе выбранного вами пресета стратегий, автоматически будет протестриована каждая стратегия в пресете,
  если какая-то из стратегий не работает, будет подобрана рабочая стратегия и создан новый файл с пресетом стратегий и припиской adjusted в конце.
  - **`Run Block Checker`** - проверка пула стратегий на работоспособность, после провреки вы получите список стратегий
  которые с большой вероятностью будут работать. Почти все стратегии из списка будут работать для обычных сайтов,
  но могут не работать для YouTube или Discord, особенно для YouTUbe я рекомендую использовать стратегии с dpi-desync-fake-tls
