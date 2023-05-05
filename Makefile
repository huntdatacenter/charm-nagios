# Run `make` or `make help` command to get help

# Use one shell for all commands in a target recipe
.ONESHELL:
# Use bash shell in Make instead of sh
SHELL := /bin/bash

PYTHON := /usr/bin/python3

PROJECTPATH=$(dir $(realpath $(MAKEFILE_LIST)))
ifndef CHARM_BUILD_DIR
	CHARM_BUILD_DIR=${PROJECTPATH}/build
endif
ifdef CONTAINER
	BUILD_ARGS="--destructive-mode"
endif
METADATA_FILE="metadata.yaml"
CHARM_NAME=$(shell cat ${PROJECTPATH}/${METADATA_FILE} | grep -E '^name:' | awk '{print $$2}')

UBUNTU_VERSION = jammy
MOUNT_TARGET = /home/ubuntu/vagrant
DIR_NAME = "$(shell basename $(shell pwd))"
VM_NAME = juju-dev--$(DIR_NAME)

clean:  ## remove unneeded files
	@echo "Cleaning files"
	@git clean -ffXd -e '!.idea'
	@echo "Cleaning existing build"
	@rm -rf ${PROJECTPATH}/${CHARM_NAME}.charm
	@rm -rf ${CHARM_BUILD_DIR}/*
	@charmcraft clean

submodules:  ## make sure that the submodules are up-to-date
	@echo "Cloning submodules"
	@git submodule update --init --recursive

submodules-update:  ## update submodules to latest changes on remote branch
	@echo "Pulling latest updates for submodules"
	@git submodule update --init --recursive --remote --merge

build: clean submodules-update  ## build the charm
	@echo "Building charm to base directory ${PROJECTPATH}/${CHARM_NAME}.charm"
	@-git rev-parse --abbrev-ref HEAD > ./repo-info
	@-git describe --always > ./version
	@charmcraft -v pack ${BUILD_ARGS}
	@bash -c ./rename.sh
#	@mkdir -p ${CHARM_BUILD_DIR}/${CHARM_NAME}
#	@unzip ${PROJECTPATH}/${CHARM_NAME}.charm -d ${CHARM_BUILD_DIR}/${CHARM_NAME}

release: clean build  ## run clean and build targets
	@charmcraft upload ${CHARM_NAME}.charm --release edge

lint:  ## run flake8 and black --check
	@echo "Running lint checks"
	@tox -e lint

black:  ## run black and reformat files
	@echo "Reformat files with black"
	@tox -e black

proof:  ## run charm proof
	@echo "Running charm proof"
	@-charm proof

unittests: submodules-update  ## run the tests defined in the unittest subdirectory
	@echo "Running unit tests"
	@tox -e unit

functional: build  ## run the tests defined in the functional subdirectory
	@echo "Executing functional tests"
	@PROJECTPATH=${PROJECTPATH} tox -e func

test: lint proof unittests functional  ## run lint, proof, unittests and functional targets
	@echo "Charm ${CHARM_NAME} has been tested"

name:  ## Print name of the VM
	echo "$(VM_NAME)"

list:  ## List existing VMs
	multipass list

launch:
	multipass launch $(UBUNTU_VERSION) -v --timeout 3600 --name $(VM_NAME) --memory 4G --cpus 4 --disk 20G --cloud-init juju.yaml \
	&& multipass exec $(VM_NAME) -- cloud-init status

mount:
	echo "Assure allowed in System settings > Privacy > Full disk access for multipassd"
	multipass mount --type 'classic' --uid-map $(shell id -u):1000 --gid-map $(shell id -g):1000 $(PWD) $(VM_NAME):$(MOUNT_TARGET)

umount:
	multipass umount $(VM_NAME):$(MOUNT_TARGET)

bootstrap:
	$(eval ARCH := $(shell multipass exec $(VM_NAME) -- dpkg --print-architecture))
	multipass exec $(VM_NAME) -- juju bootstrap localhost lxd --bootstrap-constraints arch=$(ARCH) \
	&& multipass exec $(VM_NAME) -- juju add-model default

up: launch mount bootstrap ssh  ## Start a VM

fwd:  ## Forward unit port: make unit=nagios/0 port=80 fwd
	$(eval port := 80)
	$(eval VMIP := $(shell multipass exec $(VM_NAME) -- hostname -I | cut -d' ' -f1))
	echo "Opening browser: http://$(VMIP):8000"
	bash -c "(sleep 1; open 'http://$(VMIP):8000') &"
	multipass exec $(VM_NAME) -- juju ssh $(unit) -N -L 0.0.0.0:8000:0.0.0.0:$(port)

down:  ## Stop the VM
	multipass down $(VM_NAME)

ssh:  ## Connect into the VM
	multipass exec -d $(MOUNT_TARGET) $(VM_NAME) -- bash

destroy:  ## Destroy the VM
	multipass delete -v --purge $(VM_NAME)

bridge:
	sudo route -nv add -net 192.168.64.0/24 -interface bridge100
	# Delete if exists: sudo route -nv delete -net 192.168.64.0/24

# Display target comments in 'make help'
help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {sub("\\\\n",sprintf("\n%22c"," "), $$2);printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
# Set default goal
.DEFAULT_GOAL := help

# The targets below don't depend on a file
.PHONY: help submodules submodules-update clean build release lint black proof unittests functional test
