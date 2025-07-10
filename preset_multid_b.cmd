set "BIN=%~dp0bin\"
set "LISTS=%~dp0lists\"

start "zapret: http,https,quic" /min "%BIN%winws.exe" --wf-tcp=80,443 --wf-udp=443,50000-50099 ^
--filter-tcp=80 --hostlist="%LISTS%list-general.txt" --dpi-desync=fake,fakedsplit --dpi-desync-split-pos=midsld --dpi-desync-fooling=badseq --new ^
--filter-tcp=443 --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fake,multidisorder --dpi-desync-split-pos=midsld --dpi-desync-repeats=11 --dpi-desync-fooling=badseq --dpi-desync-fake-tls-mod=rnd,dupsid,sni=www.google.com --new ^
--filter-tcp=443 --hostlist="%LISTS%list-general.txt" --dpi-desync=fake,multidisorder --dpi-desync-split-pos=midsld --dpi-desync-repeats=6 --dpi-desync-fooling=badseq --dpi-desync-fake-tls-mod=rnd,dupsid,sni=www.yandex.ru --new ^
--filter-tcp=443 --ipset="%LISTS%ipset_all.txt" --dpi-desync=fake,multidisorder --dpi-desync-split-pos=midsld --dpi-desync-repeats=6 --dpi-desync-fooling=badseq --dpi-desync-fake-tls-mod=rnd,dupsid,sni=www.yandex.ru --new ^
--filter-udp=443 --ipset="%LISTS%ipset_all.txt" --dpi-desync=fake --dpi-desync-repeats=11 --new ^
--filter-udp=443 --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fake --dpi-desync-repeats=20 --dpi-desync-fake-quic="%~dp0bin\quic_initial_www_google_com.bin" --new ^
--filter-udp=443 --hostlist="%LISTS%list-general.txt" --dpi-desync=fake --dpi-desync-repeats=11 --new ^
--filter-udp=50000-50099 --filter-l7=discord,stun --dpi-desync=fake
