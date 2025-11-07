set "BIN=%~dp0bin\"
set "LISTS=%~dp0lists\"
cd /d %BIN%

start "zapret: http,https,quic" /min "%BIN%winws.exe" --wf-tcp=80,443 ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.discord_media.txt" ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.stun.txt" ^
--wf-raw-part=@"%~dp0windivert.filter\windivert_part.quic_initial_ietf.txt" ^
--filter-tcp=80 --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fakedsplit --dpi-desync-fooling=badseq --dpi-desync-badseq-increment=2 --dpi-desync-split-pos=method+2 --new ^
--filter-tcp=80 --hostlist="%LISTS%list-general.txt" --dpi-desync=fakedsplit --dpi-desync-fooling=badseq --dpi-desync-badseq-increment=2 --dpi-desync-split-pos=method+2 --new ^
--filter-tcp=443 --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fake,hostfakesplit --dpi-desync-fooling=md5sig --dpi-desync-fake-tls="%~dp0bin\tls_clienthello_www_google_com.bin" --new ^
--filter-tcp=443 --hostlist="%LISTS%list-general.txt" --dpi-desync=fake,hostfakesplit --dpi-desync-fooling=badseq --dpi-desync-badseq-increment=2 --dpi-desync-fake-tls="%~dp0bin\tls_clienthello_www_google_com.bin" --new ^
--filter-tcp=443 --ipset="%LISTS%ipset-all.txt" --dpi-desync=fake,hostfakesplit --dpi-desync-fooling=ts --dpi-desync-fake-tls="%~dp0bin\tls_clienthello_www_google_com.bin" --new ^
--filter-l7=quic --hostlist="%LISTS%list-youtube.txt" --dpi-desync=fake --dpi-desync-repeats=11 --dpi-desync-fake-quic="%~dp0bin\quic_initial_www_google_com.bin" --new ^
--filter-l7=quic --hostlist="%LISTS%list-general.txt" --dpi-desync=fake --dpi-desync-repeats=11 --dpi-desync-fake-quic="%~dp0bin\quic_initial_www_google_com.bin" --new ^
--filter-l7=discord,stun --dpi-desync=fake --dpi-desync-repeats=6