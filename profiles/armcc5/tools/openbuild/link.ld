/* link.ld: the open-toolchain build's layout (openbuild.py fills in the @NAMES@).
   Code goes anywhere in the flash: the differential tests match functions by name. A section
   named .vectors (a startup file's vector table) goes first. RAM is ram.ld: the original's
   layout from ramlayout.py, or every variable in the order the linker likes without it. */
MEMORY
{
    FLASH (rx)  : ORIGIN = @FLASH_ORIGIN@, LENGTH = @FLASH_LENGTH@
    RAM   (rwx) : ORIGIN = @RAM_ORIGIN@, LENGTH = @RAM_LENGTH@
}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS
{
    .text : {
        KEEP(*(.vectors))
        KEEP(*(.text .text.*))
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > FLASH
    INCLUDE ram.ld
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) }
}
