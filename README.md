首先下载virtual box的deb安装包
sudo apt install -y /home/songyou/projects/mavin-virtualbox/virtualbox-7.2_7.2.6-172322~Ubuntu~noble_amd64.deb

sudo vboxmanage extpack install --replace Oracle_VirtualBox_Extension_Pack-7.2.6.vbox-extpack

 sudo mkdir -p /home/data/vbox_vms
sudo chown -R $USER:$USER /home/data/vbox_vms

vboxmanage createvm --name "Win11VM" --ostype "Windows11_64" --register --basefolder /home/data/vbox_vms


vboxmanage modifyvm "Win11VM" --memory 8192 --cpus 4 --vram 128 --graphicscontroller vboxsvga --firmware efi --tpm-type 2.0 --vrde on --vrdeport 3399

vboxmanage storagectl "Win11VM" --name "SATA" --add sata --controller IntelAhci

vboxmanage createmedium disk --filename /home/data/vbox_vms/Win11VM/Win11VM.vdi --size 61440 --format VDI

Upload iso file：
vboxmanage storageattach "Win11VM" --storagectl "IDE" --port 0 --device 0 --type dvddrive --medium /home/songyou/projects/mavin-virtualbox/zh-cn_windows_11_business_editions_version_25h2_updated_feb_2026_x64_dvd_7bd4278f.iso

vboxmanage startvm "Win11VM" --type headless


# check kvm is using by other progress or not
sudo lsof /dev/kvm 
sudo virsh list --all

# if yes
sudo rmmod kvm_intel

vboxmanage startvm "Win11VM" --type headless
