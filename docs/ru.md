# Release Link Check

Утилита находит прямые ссылки на файлы GitHub Releases в README, документации и командах установки. Если в релизе нет файла с таким именем, она укажет строку со ссылкой и вернёт ненулевой код. Для ссылок вида `latest/download` можно проверить черновик будущего релиза.

[Скачать скрипт](https://github.com/Bratuha1/release-link-check/releases/latest/download/release_link_check.py)

Нужен Python 3.10 или новее.

```powershell
python release_link_check.py README.md
```

Чтобы проверить черновик, укажите его числовой ID и задайте `GH_TOKEN` или `GITHUB_TOKEN` с доступом к этому релизу.

```powershell
python release_link_check.py README.md --candidate OWNER/REPO=123456789
```

Утилита запрашивает метаданные GitHub API. Файлы релиза она не скачивает. Для подключения к CI есть [пример GitHub Actions](../README.md#run-in-github-actions).
