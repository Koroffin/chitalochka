# SpeedReader для Android

Дата: 2026-11-01. Статус: согласован 2026-11-01.

## Зачем

Та же читалка на телефоне: библиотека книг с loveread.ec, бегущая строка с
подсветкой, позиция чтения сохраняется. Управление только касаниями, без
панели с кнопками. APK собирает GitHub Actions и кладёт в релиз рядом с exe и
deb; пользователь ставит его на телефон вручную.

## Цели

- Один пакет `speedreader`: модель чтения, хранилище, парсер loveread,
  `TickerView` используются без изменений. Для телефона отдельный набор
  страниц и своя точка входа; настольное приложение не меняет поведения.
- Чтение: тап запускает, тап останавливает; свайп слева направо отматывает
  на предложение; скорость фиксированная, 200 слов в минуту.
- Библиотека: тап открывает книгу, долгое нажатие даёт меню; добавление книги
  по ссылке как на десктопе.
- Экран не гаснет, пока приложение открыто.
- Сборка APK в CI по тегу `v*` и по ручному запуску, без локальной установки
  Android-инструментов.

## Не цели

- Google Play, подпись release-ключом, aab: APK подписан debug-ключом, ставится
  как «из неизвестного источника».
- Импорт JSON и PDF на телефоне: кнопки скрыты.
- Закладки на телефоне: интерфейса нет, существующие закладки в `library.json`
  не трогаем.
- Регулировка скорости, тёмная тема, планшетная раскладка.
- Сборка под x86_64 и эмулятор: только arm64-v8a.

## Анти-цели

- Сломать десктоп: тесты десктопа проходят без изменений, кроме тех, что
  расширяют `TickerView` (см. «Шрифт»).
- Молчаливый сбой сборки: если патчи python-for-android не легли, сборка
  падает с понятным сообщением, а не выпускает APK без кнопки «Назад».

## Ограничения инструментов (проверено 2026-11-01)

- Официальные Android-колёса PySide6 6.12.0 с тегом `cp310-abi3`
  (один файл для Python 3.10–3.14), только aarch64 и x86_64:
  `https://download.qt.io/official_releases/QtForPython/pyside6/pyside6-6.12.0-6.12.0-cp310-abi3-android_aarch64.whl`
  и такой же `shiboken6` в папке `shiboken6/`.
- `pyside6-android-deploy` работает только на Linux/macOS, хост-Python ≥ 3.10,
  JDK 17+, NDK r28c (`28.2.13676358`), SDK: `platform-tools`,
  `platforms;android-35`, `build-tools;35.0.0`. Точка входа обязана
  называться `main.py`. На устройстве CPython 3.14, `PYTHONOPTIMIZE=2`
  (assert и docstring вырезаны, на них нельзя опираться).
- Раннер `ubuntu-24.04`: SDK в `$ANDROID_HOME`, JDK 17 по умолчанию, NDK 27.3
  по умолчанию (r28c ставится через `sdkmanager`), Python 3.12 есть.
- Опорная реализация, с которой сняты шаги сборки: репозиторий
  `github.com/Cutleast/pyside6-android-example`, коммит
  `502b8361427703d94217da5c62a2aa43e01083ce` (2026-10-30), файлы
  `scripts/build_android.py` и `.github/workflows/build_android.yaml`.
  Widgets-приложение на PySide6 6.12, его сборка в Actions проходит за
  12–14 минут с кэшем. Лицензия GPL-3.0: код оттуда не копируется, только
  порядок шагов; исполнитель читает оригинал (`gh api -H "Accept:
  application/vnd.github.raw" repos/Cutleast/pyside6-android-example/contents/<путь>?ref=<коммит>`)
  и пишет своё.
- Наблюдение по опорной реализации: `pyside6-android-deploy --init --force
  --keep-deployment-files --no-install` оставляет в рабочей папке
  `pysidedeploy.spec`, `buildozer.spec` и `deployment/recipes/PySide6/__init__.py`;
  её скрипт читает их сразу после `--init`, и сборка зелёная.
- Python из python-for-android не находит системные корневые сертификаты
  (гипотеза из практики p4a: `urlopen` на https падает с
  `CERTIFICATE_VERIFY_FAILED`). Поэтому единственная внешняя зависимость
  APK: `certifi`.
- Кэш `actions/cache` виден запуску только с того же ref или с ветки по
  умолчанию, а сборки идут по тегам: ключ подписи нельзя держать в кэше.
- Qt-бутстрап python-for-android перехватывает кнопку «Назад» («нажмите ещё
  раз для выхода») и не отдаёт её Qt; его `PythonActivity.java` надо
  пропатчить. Там же есть опция `wakelock`: при `android.wakelock = True`
  активити держит `SCREEN_BRIGHT_WAKE_LOCK` всё время работы приложения.
- Qt для Android собран без zstd; у нас нет qrc-ресурсов, иконка грузится из
  png файлом.
- Зависимости приложения: стандартная библиотека, PySide6 и `certifi`
  (только на Android). Других `requirements` в buildozer.spec нет.

## Устройство приложения

### Точка входа и определение платформы

- `speedreader/mobile.py`: `MobileWindow(store)` на базе `QStackedWidget` с
  тремя страницами и функция `main()`: папка данных из
  `QStandardPaths.AppDataLocation` (как `default_data_dir()` на десктопе),
  ошибка хранилища при старте показывается `QMessageBox.critical`, как на
  десктопе. Окно показывается `showMaximized()`.
- `packaging/android/main.py`: две строки, импортирует `speedreader.mobile`
  и вызывает `main()`.
- Android определяется как `hasattr(sys, "getandroidapilevel")`.
  `speedreader.idle.platform_backend()` на Android возвращает `NullBackend`:
  там `sys.platform == "linux"`, а QtDBus в APK нет. Экран держит wakelock
  активити (см. сборку).
- На Android `main()` до первого обращения к сети выставляет переменную
  окружения `SSL_CERT_FILE` в `certifi.where()`, если она не задана; на
  десктопе `certifi` не импортируется.

### Общее для экранов

- Элементы под палец: строки списка и кнопки не ниже 48 px логических.
- Системная кнопка «Назад» приходит в Qt как `Qt.Key_Back`; `MobileWindow`
  обрабатывает её сам (на читалке и на добавлении), на библиотеке отдаёт
  системе: Qt для Android закрывает активити, приложение завершается. Это
  допустимо: всё, что надо сохранить, уже записано при паузе и при выходе
  из читалки.
- Уход приложения в фон (`QGuiApplication.applicationStateChanged`, состояние
  не `ApplicationActive`) ставит чтение на паузу, а пауза сохраняет позицию.

### Библиотека

- Сверху строка: подпись «Библиотека» слева, кнопка «Добавить» справа.
- Список книг: текст строки тот же, что на десктопе (`название — автор —
  NN %`, либо `файл книги не найден`). Пустая библиотека: та же подпись, что
  на десктопе.
- Тап по доступной книге открывает её с сохранённой позиции. Тап по
  недоступной ничего не делает.
- Долгое нажатие (свой таймер 500 мс от нажатия; сдвиг больше 20 px или
  отпускание раньше отменяют) открывает `QMenu` у точки нажатия:
  «Начать заново» (только доступная книга), «Скачать заново» (только если
  `detect(source_url)` знает источник), «Удалить» (с подтверждением
  `QMessageBox.question`, как на десктопе). Действия те же вызовы хранилища,
  что в `LibraryPage`: переиспользование через общий помощник или
  наследование на усмотрение исполнителя, поведение десктопа не меняется.
- Ошибки хранилища показываются `QMessageBox.warning` (строки состояния на
  телефоне нет).

### Добавление книги

- Переиспользуется `AddPage`. На Android кнопки «Импорт JSON» и «Загрузить
  PDF» скрыты. Остальное как на десктопе: поле ссылки, «Скачать», прогресс,
  «Отмена», «Назад».
- Кнопка «Назад» телефона ведёт себя как Esc на десктопе: возвращает в
  библиотеку, если загрузка не идёт; во время загрузки ничего не делает.
- Готовая книга открывается сразу в читалке, как на десктопе.

### Читалка

- Экран: сверху тонкая полоса прогресса (4 px, без текста, доля
  `model.progress`), ниже `TickerView` на всю оставшуюся площадь. Никаких
  кнопок, слайдеров, подписей.
- Скорость: константа `ANDROID_WPM = 200` в `speedreader/mobile.py`.
  `store.wpm` на телефоне не читается и не пишется. Задержки те же, что на
  десктопе при 200 сл/мин: веса длины, паузы на знаках, предлоги склеены.
- Жесты различаются по отпусканию пальца:
  - сдвиг меньше 20 px: тап, переключает старт/пауза;
  - сдвиг по горизонтали не меньше 1/5 ширины экрана вправо и
    `|dx| > 2·|dy|`: отмотка, `model.prev_sentence()` (как ← на десктопе);
    если чтение шло, оно продолжается с нового места, таймер перезапускается;
    если стояло, позиция сохраняется сразу;
  - всё остальное: ничего.
- Кнопка «Назад»: сохранить позицию, вернуться в библиотеку (список
  обновляется, процент меняется).
- Заголовки жирным, ¶ в конце абзаца: как на десктопе, через `TickerView`.
- Старт в конце книги ничего не делает (как на десктопе).

### Шрифт

`TickerView` подбирает кегль под ширину: начиная с 28 pt, уменьшает шагом 2 pt
до минимума 14 pt, пока текущая группа (с учётом жирного) не укладывается в
90 % ширины виджета. Соседние слова рисуются тем же кеглем и обрезаются, как
сейчас. На десктопе при ширине 960 px это срабатывает только на аномально
длинных группах; тесты десктопа не меняются, кроме добавления теста на
уменьшение.

## Сборка APK

### Скрипт `packaging/build_android.py`

Python, стандартная библиотека плюс PySide6 хоста. Аргументы: `--sdk-path`,
`--ndk-path`, `--version` (PEP 440, как выдаёт `set_version.py`),
`--out` (папка результата), `--arch` (по умолчанию `aarch64`). Шаги:

1. Staging `build/android/app/`: `main.py` из `packaging/android/`, пакет
   `speedreader/` без `__pycache__` (с `assets/icon.png`). Папка
   `.buildozer` внутри staging сохраняется между сборками ради кэша.
2. Колёса `pyside6` и `shiboken6` версии хостового `PySide6.__version__`
   скачиваются по адресам выше в `build/android/wheels/`, повторно не
   качаются.
3. `pyside6-android-deploy --init --force --verbose --keep-deployment-files
   --no-install --name=speedreader --wheel-pyside=… --wheel-shiboken=…
   --sdk-path=… --ndk-path=…` в staging. Проверка: в `pysidedeploy.spec`
   секция `qt.modules` содержит `Widgets`.
4. Урезание рецепта PySide6. Сгенерированный рецепт копирует колесо целиком
   дважды: в нативные библиотеки APK и в питоновский пакет. Скрипт дописывает
   в `deployment/recipes/PySide6/__init__.py` подкласс рецепта, который после
   `build_arch` родителя:
   - читает из `pysidedeploy.spec` списки `qt.modules`, `android.plugins`,
     `buildozer.local_libs`;
   - в папке нативных библиотек (`ctx.get_libs_dir(arch)`) оставляет
     `libQt6<Module>_<arch>.so` для модулей из списка,
     `libplugins_<plugin>_<arch>.so` для плагинов и `local_libs`, остальные
     копии `lib*.so` из `PySide6/Qt/lib` удаляет;
   - в питоновском пакете `PySide6` (`ctx.get_python_install_dir(arch)`)
     удаляет папки `Qt`, `include`, `typesystems`, `glue`, `scripts` и
     биндинги `Qt<Module>.abi3.so` с их `.pyi` для модулей не из списка.
   Qt на Android грузит библиотеки и плагины из нативных библиотек APK, не
   из пакета.
5. python-for-android: checkout по коммиту `p4a.commit` из сгенерированного
   `buildozer.spec` в `build/android/python-for-android`, два патча
   `PythonActivity.java`: перед строкой `Log.v(TAG, "About to do super
   onCreate");` добавить `setEnvironmentVariable("QT_ANDROID_DISABLE_ACCESSIBILITY", "1");`
   (мост доступности Qt зависает при всплывающих окнах, когда включён
   менеджер паролей), и удалить обработчик `onKeyDown` с `lastBackClick`.
   Если якорь или обработчик не найдены, скрипт падает с текстом, какой
   патч не лёг.
6. Правка `buildozer.spec` (секция `app`): `title = SpeedReader`,
   `package.name = speedreader`, `package.domain = io.github.koroffin`,
   `version = <--version>`, удалить `version.regex` и `version.filename`,
   `android.numeric_version = 1 + major·1000000 + minor·10000 + patch·100`,
   `requirements = <то, что сгенерировано>,certifi`,
   `android.permissions = android.permission.INTERNET`,
   `android.wakelock = True`, `orientation = portrait`,
   `icon.filename = %(source.dir)s/speedreader/assets/icon.png`,
   `android.archs = arm64-v8a`, `android.api = 35`, `android.minapi = 28`,
   `android.accept_sdk_license = True`, `android.skip_update = True`,
   `android.allow_backup = False`,
   `source.exclude_dirs = deployment,bin,.buildozer`,
   `p4a.source_dir = <checkout из шага 5>`.
7. `python -m buildozer android debug` в staging; самый свежий `*.apk`
   копируется в `--out` под именем `speedreader_<version>_arm64-v8a.apk`.

`android.numeric_version` одинаков для `1.2.0rc1` и `1.2.0`: Android ставит
APK с равным кодом поверх установленного, это не понижение. Ручные сборки
(`0.0.0.devN`, код 1) нужны как артефакт CI для проверки сборки; поверх
релиза они не встанут, это принято.

### Ключ подписи

Debug-APK подписывается ключом из репозитория:
`packaging/android/debug.keystore`, создаётся один раз командой
`keytool -genkeypair -keystore debug.keystore -storepass android -keypass
android -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000
-dname "CN=Android Debug,O=Android,C=US"` и коммитится. Workflow кладёт его в
`$ANDROID_USER_HOME/debug.keystore`, откуда Gradle берёт debug-ключ. Это не
секрет: обычный debug-ключ Android устроен так же; он нужен только затем,
чтобы каждый новый APK ставился поверх прошлого и библиотека на телефоне
не терялась (`allow_backup = False`, данные в приватной папке приложения,
удаление приложения стирает их).

Функции патчей и правки spec чистые (строка на входе, строка на выходе) и
покрыты тестами в `tests/test_packaging.py`; сам buildozer в тестах не
запускается.

### Workflow

Новая job `build-android` в `.github/workflows/build.yml`, `needs: test`,
`runs-on: ubuntu-24.04`, `timeout-minutes: 90`:

- `actions/setup-java@v5` temurin 17; apt: `autoconf automake libtool
  libltdl-dev pkg-config zip unzip cmake libffi-dev libssl-dev zlib1g-dev`;
  `sdkmanager` ставит `platform-tools`, `platforms;android-35`,
  `build-tools;35.0.0`, `ndk;28.2.13676358`, принимает лицензии и делает
  симлинк `$ANDROID_HOME/tools → cmdline-tools/latest` (buildozer ищет
  инструменты по старому пути).
- Отдельное venv Python 3.12 вне проекта: `pyside6==6.12.0` (хостовая версия
  равна версии колёс), `buildozer==1.6.0`, `cython==0.29.33`, затем
  `-r <PySide6>/scripts/requirements-android.txt`.
- `ANDROID_USER_HOME` задан явно, туда копируется `packaging/android/debug.keystore`.
- Кэш `actions/cache@v4`: `~/.buildozer`, `build/android/app/.buildozer`,
  `build/android/wheels` с ключом от хэша `packaging/build_android.py`.
  Кэш ускоряет, но не нужен для корректности: по тегам он чаще всего пуст.
- Версия через `packaging/set_version.py`, как в других job, скрипту
  передаётся `pep440`.
- Дымовая проверка: `$ANDROID_HOME/build-tools/35.0.0/aapt dump badging
  out/*.apk` содержит `package: name='io.github.koroffin.speedreader'` и
  `android.permission.INTERNET`.
- Артефакт `speedreader-apk`; job `release` добавляет `build-android` в
  `needs` и `*.apk` в файлы релиза; в `body` релиза строка про Android:
  ставить через «неизвестные источники», ставится поверх прошлой версии.
- Первая сборка без кэша 20–30 минут, дальше 12–15; релиз выходит после
  неё.

## Тесты

- `tests/test_mobile.py` (headless, как остальные Qt-тесты): тап открывает
  доступную книгу и не открывает недоступную; долгое нажатие показывает меню
  с нужным набором пунктов, «Начать заново» сбрасывает позицию; в читалке
  тап запускает и останавливает, короткое протягивание считается тапом;
  свайп вправо вызывает отмотку на предложение и не меняет состояние
  старт/пауза; свайп вверх ничего не делает; `Key_Back` сохраняет позицию и
  возвращает в библиотеку; уход в фон ставит на паузу; задержка равна
  `delay_for` при 200 независимо от `store.wpm`; на добавлении скрыты
  «Импорт JSON» и PDF; `Key_Back` на добавлении возвращает в библиотеку.
- `tests/test_reader_page.py`: длинная группа уменьшает кегль, короткая
  остаётся на 28 pt.
- `tests/test_idle.py`: при признаке Android `platform_backend()` даёт
  `NullBackend`.
- `tests/test_mobile.py`: при признаке Android и незаданной `SSL_CERT_FILE`
  `main()`-подготовка окружения выставляет её в путь `certifi` (тест с
  подменённым `certifi.where`).
- `tests/test_packaging.py`: функции правки `buildozer.spec` и патчей
  `PythonActivity.java` на фрагментах-фикстурах, включая отказ при
  отсутствии якоря; имя APK; workflow содержит `build-android` и `*.apk` в
  релизе.
- На устройстве в CI ничего не проверяется. После первого релиза
  пользователь ставит APK и проверяет: запуск, добавление книги по ссылке,
  тап/свайп, кнопку «Назад», что экран не гаснет за время чтения, что
  позиция сохраняется после сворачивания.

## Решения, принятые без обсуждения

- Размер отмотки: предложение (подтверждено пользователем).
- Сборка только в GitHub Actions, проверка на телефоне пользователя
  (подтверждено).
- Библиотека: тап открывает, долгое нажатие даёт меню (подтверждено).
- Полоса прогресса вместо процента текстом; экран без текста кроме слов.
- Свайп влево не занят.
- Wakelock на всё приложение, а не только на чтение: проще и надёжнее, чем
  Java-мост, которого в PySide6 нет.
- Debug-подпись ключом из репозитория (см. «Ключ подписи»).
- `certifi` в APK и `SSL_CERT_FILE` на старте.
- «Назад» на библиотеке закрывает приложение.
- Код версии одинаков у rc и релиза; ручные сборки на телефон не ставятся.
- Пакет `io.github.koroffin.speedreader`.

## Что остаётся исполнителю

- Как именно переиспользовать логику `LibraryPage` (помощник или наследник).
- Реализация долгого нажатия и распознавания жестов (таймер и mouse-события
  Qt; на Android касания приходят как mouse-события).
- Точный способ рисовать полосу прогресса.
- Разбиение `build_android.py` на функции и названия.

## Риски

- Поведение Qt Widgets на телефоне (масштаб шрифтов, вид списка, клавиатура
  на поле ссылки) проверяется только на устройстве; вероятна вторая
  итерация по размерам.
- Якоря патчей python-for-android могут уехать при смене закреплённого
  коммита; сборка упадёт явно, патч придётся обновить.
- Время сборки в CI удлиняет выпуск релиза на 15–30 минут.
