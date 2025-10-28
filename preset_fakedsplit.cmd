set "BIN=%~dp0bin\"
set "LISTS=%~dp0lists\"
cd /d %BIN%

start "zapret: http,https,quic" /min "%BIN%winws.exe" --wf-tcp=80,443 ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.discord_media.txt" ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.stun.txt" ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.quic_initial_ietf.txt" ^
--filter-tcp=80 --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fakedsplit --dpi-desync-fooling=badseq --dpi-desync-badseq-increment=0 --dpi-desync-split-pos=midsld --new ^
--filter-tcp=80 --hostlist="%LISTS%list-general.txt" --dpi-desync=fakedsplit --dpi-desync-fooling=badseq --dpi-desync-badseq-increment=0 --dpi-desync-split-pos=midsld --new ^
--filter-tcp=443 --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fake,fakedsplit --dpi-desync-fooling=badseq --dpi-desync-split-pos=midsld --dpi-desync-fake-tls="%~dp0bin\tls_clienthello_www_google_com.bin" --new ^
--filter-tcp=443 --hostlist="%LISTS%list-general.txt" --dpi-desync=fake --dpi-desync-fooling=badseq --dpi-desync-badseq-increment=0 --dpi-desync-fake-tls-mod=rnd,dupsid,sni=ya.ru --new ^
--filter-tcp=443 --ipset="%LISTS%ipset-cloud.txt" --dpi-desync=fake,fakedsplit --dpi-desync-ttl=1 --dpi-desync-autottl=-1 --dpi-desync-split-pos=midsld --dpi-desync-fake-tls="%~dp0bin\tls_clienthello_www_google_com.bin" --new ^
--filter-l7=quic --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fake --dpi-desync-repeats=11 --dpi-desync-fake-quic="%~dp0bin\quic_initial_www_google_com.bin" --new ^
--filter-l7=quic --hostlist="%LISTS%list-general.txt" --dpi-desync=fake --dpi-desync-repeats=11 --dpi-desync-fake-quic="%~dp0bin\quic_initial_www_google_com.bin" --new ^
--filter-l7=discord,stun --dpi-desync=fake --dpi-desync-repeats=6